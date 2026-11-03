"""ASCOM Alpaca Telescope device: protocol envelope, errors, GoTo/Sync/Tracking, control authority, safety, auth,
discovery, and an end-to-end run with alpyca (the ASCOM Initiative's own Python client) when it is installed."""

from __future__ import annotations

import asyncio
import base64
import json
import socket
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from openskymount.mount.geometry import separation
from openskymount.server.alpaca import CLIENT_IDLE_S, start_discovery
from openskymount.server.app import Observatory, create_app

from .conftest import T0
from .test_server import Cmd, recv_until

BASE = "/api/v1/telescope/0"
CAPELLA = "hip:24608"


@pytest.fixture
def obs(tmp_path) -> Observatory:
    o = Observatory(state_dir=tmp_path)
    o.clock.set_time(T0)
    o.clock.set_rate(20.0)  # fast slews; the controller is deterministic in simulation time
    return o


@pytest.fixture
def client(obs: Observatory) -> Iterator[TestClient]:
    with TestClient(create_app(observatory=obs)) as c:
        yield c


class Alpaca:
    def __init__(self, http: TestClient, client_id: int = 7) -> None:
        self.http = http
        self.cid = client_id
        self.tid = 0

    def _params(self, extra: dict[str, Any]) -> dict[str, Any]:
        self.tid += 1
        return {"ClientID": self.cid, "ClientTransactionID": self.tid, **extra}

    def get(self, member: str, **params: Any) -> dict[str, Any]:
        r = self.http.get(f"{BASE}/{member}", params=self._params(params))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ClientTransactionID"] == self.tid
        return body

    def put(self, member: str, **params: Any) -> dict[str, Any]:
        data = {k: (str(v).lower() if isinstance(v, bool) else v) for k, v in self._params(params).items()}
        r = self.http.put(f"{BASE}/{member}", data=data)
        assert r.status_code == 200, r.text
        return r.json()

    def value(self, member: str, **params: Any) -> Any:
        body = self.get(member, **params)
        assert body["ErrorNumber"] == 0, body
        return body["Value"]

    def ok(self, member: str, **params: Any) -> None:
        body = self.put(member, **params)
        assert body["ErrorNumber"] == 0, body

    def wait_idle(self, timeout: float = 30.0) -> None:
        end = time.time() + timeout
        time.sleep(0.2)
        while self.value("slewing"):
            assert time.time() < end, "slew did not finish"
            time.sleep(0.1)


def jnow_of(obs: Observatory, target_id: str) -> tuple[float, float]:
    """Apparent RA (hours) / Dec of date of a catalog object, as a planetarium program would send it."""
    rd = obs.engine.radec(obs.engine.resolve(target_id), obs.clock.now())
    return rd["ra_date"] / 15.0, rd["dec_date"]


def test_management_api_and_envelope(client: TestClient) -> None:
    r = client.get("/management/apiversions", params={"ClientTransactionID": 5}).json()
    assert r["Value"] == [1] and r["ClientTransactionID"] == 5 and r["ErrorNumber"] == 0
    d = client.get("/management/v1/description").json()["Value"]
    assert d["ServerName"] == "OpenSkyMount"
    devs = client.get("/management/v1/configureddevices").json()["Value"]
    assert len(devs) == 1 and devs[0]["DeviceType"] == "Telescope" and devs[0]["DeviceNumber"] == 0
    assert len(devs[0]["UniqueID"]) == 36
    # Parameter names are case-insensitive; transaction ids echo / increase.
    a = client.get(f"{BASE}/name", params={"clientid": 1, "clienttransactionid": 77}).json()
    b = client.get(f"{BASE}/name", params={"CLIENTID": 1, "CLIENTTRANSACTIONID": 78}).json()
    assert a["Value"] == "OpenSkyMount" and a["ClientTransactionID"] == 77 and b["ClientTransactionID"] == 78
    assert b["ServerTransactionID"] > a["ServerTransactionID"] > 0
    assert client.get(f"{BASE}/interfaceversion").json()["Value"] == 4
    assert client.get("/setup", follow_redirects=False).status_code in (302, 307)


def test_protocol_errors(client: TestClient) -> None:
    al = Alpaca(client)
    assert client.get("/api/v1/telescope/1/name").status_code == 404
    assert client.get(f"{BASE}/nosuchproperty").status_code == 400
    assert client.put(f"{BASE}/connected", data={"Connected": "maybe"}).status_code == 400
    assert client.put(f"{BASE}/connected", data={}).status_code == 400
    # Telescope members need a connection; common members do not.
    assert al.get("rightascension")["ErrorNumber"] == 0x407
    assert al.put("slewtocoordinatesasync", RightAscension=1, Declination=2)["ErrorNumber"] == 0x407
    assert al.value("connected") is False and al.value("driverversion")
    al.ok("connected", Connected=True)
    assert al.value("connected") is True
    assert al.get("connected")["Value"] is True
    assert Alpaca(client, client_id=8).value("connected") is False  # connection state is per client
    assert al.put("park")["ErrorNumber"] == 0x400
    assert al.put("moveaxis", Axis=0, Rate=1.0)["ErrorNumber"] == 0x400
    assert al.get("sideofpier")["ErrorNumber"] == 0x400
    assert al.put("action", Action="x", Parameters="")["ErrorNumber"] == 0x40C
    assert al.get("canmoveaxis", Axis=5)["ErrorNumber"] == 0x401
    assert al.value("canmoveaxis", Axis=0) is False and al.value("axisrates", Axis=1) == []
    assert al.get("targetrightascension")["ErrorNumber"] == 0x402
    assert al.put("targetrightascension", TargetRightAscension=25)["ErrorNumber"] == 0x401
    al.ok("targetrightascension", TargetRightAscension=5.5)
    assert al.value("targetrightascension") == 5.5
    assert al.put("slewtocoordinatesasync", RightAscension=24.0, Declination=0)["ErrorNumber"] == 0x401
    assert al.put("slewtocoordinatesasync", RightAscension=1.0, Declination=91)["ErrorNumber"] == 0x401
    assert al.put("trackingrate", TrackingRate=1)["ErrorNumber"] == 0x401
    al.ok("trackingrate", TrackingRate=0)
    assert al.value("alignmentmode") == 0 and al.value("equatorialsystem") == 1
    assert al.value("trackingrates") == [0] and al.value("canslewasync") and not al.value("canslew")
    names = {s["Name"] for s in al.value("devicestate")}
    assert {"RightAscension", "Declination", "Altitude", "Azimuth", "Slewing", "Tracking", "TimeStamp"} <= names


def test_position_matches_engine(client: TestClient, obs: Observatory) -> None:
    al = Alpaca(client)
    al.ok("connected", Connected=True)
    alt, az = al.value("altitude"), al.value("azimuth")
    ra, dec = al.value("rightascension"), al.value("declination")
    ctl = obs.controller
    e_alt, e_az = ctl.pointing()
    assert abs(alt - e_alt) < 1e-6 and abs(az - e_az % 360) < 1e-6
    # RA/Dec of date of that direction, converted back with the engine, gives the same alt/az.
    ra_icrs, dec_icrs = obs.engine.apparent_to_icrs(ra * 15, dec, ctl.sim_time)
    a2, z2 = obs.engine.altaz(obs.engine.resolve(f"radec:{ra_icrs},{dec_icrs}"), ctl.sim_time)
    assert separation(float(a2), float(z2), e_alt, e_az) < 2.0 / 3600  # refraction removed and re-applied
    lst = al.value("siderealtime")
    assert abs(lst - obs.engine.local_sidereal_time_deg(obs.clock.now()) / 15) < 0.01
    assert al.value("utcdate").startswith("2026-11-01T01:")
    assert al.value("sitelatitude") == obs.config.observer.latitude


def test_goto_sync_and_tracking(client: TestClient, obs: Observatory) -> None:
    al = Alpaca(client)
    al.ok("connected", Connected=True)
    # Tracking=true follows the sky from the current direction (park: north horizon, a rising position).
    al.ok("tracking", Tracking=True)
    assert al.value("tracking")
    al.wait_idle()
    al.ok("tracking", Tracking=False)
    # Below the altitude limit there is nothing to track: refused, not faked.
    al.ok("slewtoaltazasync", Azimuth=270.0, Altitude=-3.0)
    al.wait_idle()
    r = al.put("tracking", Tracking=True)
    assert r["ErrorNumber"] == 0x40B and "altitude" in r["ErrorMessage"]
    ra, dec = jnow_of(obs, CAPELLA)
    al.ok("slewtocoordinatesasync", RightAscension=ra, Declination=dec)
    assert al.value("slewing") and al.value("tracking")
    assert al.value("targetrightascension") == ra
    al.wait_idle()
    assert al.value("tracking") and obs.controller.mode == "tracking"
    time.sleep(0.5)
    # End to end: the payload points at Capella (with its proper motion and parallax) within a few arcseconds.
    capella = obs.engine.resolve(CAPELLA)
    t = obs.controller.sim_time
    c_alt, c_az = obs.engine.altaz(capella, t)
    p_alt, p_az = obs.controller.pointing()
    assert separation(p_alt, p_az, float(c_alt), float(c_az)) < 10.0 / 3600
    ra_now, dec_now = al.value("rightascension"), al.value("declination")
    assert abs(ra_now - ra) * 15 * 3600 * 0.6 < 15 and abs(dec_now - dec) * 3600 < 15
    # Sync adds an alignment point.
    n0 = len(obs.controller.model.points)
    al.ok("synctocoordinates", RightAscension=ra, Declination=dec)
    assert len(obs.controller.model.points) == n0 + 1
    # Alt/az slews require tracking off (always satisfiable).
    assert al.put("slewtoaltazasync", Azimuth=180.0, Altitude=40.0)["ErrorNumber"] == 0x40B
    al.ok("tracking", Tracking=False)
    assert not al.value("tracking") and obs.controller.mode == "idle"
    al.ok("tracking", Tracking=True)  # tracks the current position again
    assert al.value("tracking")
    al.wait_idle()
    al.ok("tracking", Tracking=False)
    assert al.put("slewtoaltazasync", Azimuth=360.0, Altitude=40.0)["ErrorNumber"] == 0x401
    al.ok("slewtoaltazasync", Azimuth=180.0, Altitude=40.0)
    assert al.value("slewing") and not al.value("tracking")
    al.ok("abortslew")
    time.sleep(0.2)
    assert not al.value("slewing") and obs.controller.mode == "idle"
    # AbortSlew of a coordinate slew returns tracking to its pre-slew state (here: on).
    al.ok("slewtocoordinatesasync", RightAscension=ra, Declination=dec)
    al.wait_idle()
    other_ra, other_dec = jnow_of(obs, "hip:21421")  # Aldebaran
    al.ok("slewtocoordinatesasync", RightAscension=other_ra, Declination=other_dec)
    al.ok("abortslew")
    assert al.value("tracking")
    al.ok("slewtotargetasync")  # target = Aldebaran
    al.wait_idle()
    assert obs.controller.target is not None and obs.controller.mode == "tracking"


def test_safety_and_control_authority(client: TestClient, obs: Observatory) -> None:
    al = Alpaca(client)
    al.ok("connected", Connected=True)
    status = client.get("/api/status").json()
    assert any(c["name"].startswith("Alpaca #7") for c in status["control"]["clients"])
    with client.websocket_connect("/ws?name=browser") as ws:
        ui = Cmd(ws)
        assert ui.control()["granted"]
        ra, dec = jnow_of(obs, CAPELLA)
        r = al.put("slewtocoordinatesasync", RightAscension=ra, Declination=dec)
        assert r["ErrorNumber"] == 0x40B and "browser has control" in r["ErrorMessage"]
        assert ui("goto", target=CAPELLA)["ok"]
        recv_until(ws, lambda m: m["type"] == "state" and m["mount"]["mode"] == "slewing")
        al.ok("abortslew")  # a stop is accepted from every client
        assert obs.controller.mode == "idle"  # and does not resume tracking for the browser's slew
        ws.send_text(json.dumps({"type": "release_control"}))
        recv_until(ws, lambda m: m.get("type") == "control_result")
        al.ok("slewtocoordinatesasync", RightAscension=ra, Declination=dec)
        m = recv_until(ws, lambda m: m["type"] == "state" and m["control"]["controller_name"] is not None)
        assert m["control"]["controller_name"].startswith("Alpaca")
        # The browser can always take control back.
        assert ui.control(force=True)["granted"]
        assert al.put("tracking", Tracking=False)["ErrorNumber"] == 0x40B
    # Solar safety applies to Alpaca exactly as to the UI.
    obs.clock.set_time(T0 + 11 * 3600)  # noon in Berlin
    sun_ra, sun_dec = jnow_of(obs, "sun")
    r = al.put("slewtocoordinatesasync", RightAscension=sun_ra, Declination=sun_dec)
    assert r["ErrorNumber"] == 0x40B and "Sun" in r["ErrorMessage"]
    # Site changes are validated and go through the shared configuration.
    assert al.put("sitelatitude", SiteLatitude=95.0)["ErrorNumber"] == 0x401
    al.ok("sitelatitude", SiteLatitude=48.1)
    assert obs.config.observer.latitude == 48.1
    # Disconnect removes the client from the session list.
    al.ok("connected", Connected=False)
    clients = client.get("/api/status").json()["control"]["clients"]
    assert not any(c["name"].startswith("Alpaca") for c in clients)


def test_idle_alpaca_clients_are_dropped(client: TestClient) -> None:
    al = Alpaca(client)
    al.ok("connected", Connected=True)
    dev = client.app.state.alpaca  # type: ignore[attr-defined]
    dev.purge(time.time() + CLIENT_IDLE_S + 1)
    assert al.value("connected") is False
    assert al.get("rightascension")["ErrorNumber"] == 0x407


def test_password_mode_accepts_basic_auth(tmp_path) -> None:
    with TestClient(create_app(observatory=Observatory(state_dir=tmp_path), password="secret-pw")) as c:
        assert c.get(f"{BASE}/name").status_code == 401
        assert c.get("/management/v1/configureddevices").status_code == 200
        good = "Basic " + base64.b64encode(b"nina:secret-pw").decode()
        bad = "Basic " + base64.b64encode(b"nina:wrong").decode()
        assert c.get(f"{BASE}/name", headers={"Authorization": bad}).status_code == 401
        assert c.get(f"{BASE}/name", headers={"Authorization": "Basic %%%"}).status_code == 401
        assert c.get(f"{BASE}/name", headers={"Authorization": good}).json()["Value"] == "OpenSkyMount"


def test_alpaca_can_be_disabled(tmp_path) -> None:
    with TestClient(create_app(observatory=Observatory(state_dir=tmp_path), alpaca=False)) as c:
        assert c.get(f"{BASE}/name").status_code == 404


def _free_port(kind: int = socket.SOCK_STREAM) -> int:
    with socket.socket(socket.AF_INET, kind) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_discovery_responder() -> None:
    port = _free_port(socket.SOCK_DGRAM)

    async def run() -> bytes:
        transport = await start_discovery("127.0.0.1", 8123, udp_port=port)
        assert transport is not None
        loop = asyncio.get_running_loop()
        reply: asyncio.Future[bytes] = loop.create_future()

        class Probe(asyncio.DatagramProtocol):
            def datagram_received(self, data: bytes, addr: Any) -> None:
                if not reply.done():
                    reply.set_result(data)

        probe, _ = await loop.create_datagram_endpoint(Probe, remote_addr=("127.0.0.1", port))
        probe.sendto(b"not-alpaca")
        probe.sendto(b"alpacadiscovery1")
        try:
            return await asyncio.wait_for(reply, 5)
        finally:
            probe.close()
            transport.close()

    assert json.loads(asyncio.run(run())) == {"AlpacaPort": 8123}


def test_end_to_end_with_alpyca(tmp_path) -> None:
    """A real HTTP server driven by alpyca, an independent Alpaca client implementation."""
    telescope = pytest.importorskip("alpaca.telescope", reason="alpyca not installed")
    exceptions = pytest.importorskip("alpaca.exceptions")
    import uvicorn

    obs = Observatory(state_dir=tmp_path)
    obs.clock.set_time(T0)
    obs.clock.set_rate(20.0)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(observatory=obs), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        end = time.time() + 20
        while not server.started:
            assert time.time() < end and thread.is_alive(), "server did not start"
            time.sleep(0.05)
        t = telescope.Telescope(f"127.0.0.1:{port}", 0)
        with pytest.raises(exceptions.NotConnectedException):
            _ = t.RightAscension
        t.Connected = True
        assert t.InterfaceVersion == 4 and t.Name == "OpenSkyMount" and not t.CanMoveAxis(telescope.TelescopeAxes.axisPrimary)
        assert t.EquatorialSystem == telescope.EquatorialCoordinateType.equTopocentric
        assert t.AlignmentMode == telescope.AlignmentModes.algAltAz
        assert t.UTCDate.year == 2026
        ra, dec = jnow_of(obs, CAPELLA)
        t.SlewToCoordinatesAsync(ra, dec)
        end = time.time() + 30
        while t.Slewing:
            assert time.time() < end
            time.sleep(0.1)
        assert t.Tracking
        assert abs(t.RightAscension - ra) * 15 * 3600 * 0.6 < 15 and abs(t.Declination - dec) * 3600 < 15
        with pytest.raises(exceptions.InvalidValueException):
            t.SlewToCoordinatesAsync(25.0, 0.0)
        with pytest.raises(exceptions.NotImplementedException):
            t.Park()
        state = {s["Name"]: s["Value"] for s in t.DeviceState}
        assert state["Tracking"] is True
        t.Tracking = False
        assert not t.Tracking
        t.Connected = False
    finally:
        server.should_exit = True
        thread.join(10)
