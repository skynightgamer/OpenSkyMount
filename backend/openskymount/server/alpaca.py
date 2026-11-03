"""ASCOM Alpaca Telescope device (``/api/v1/telescope/0/...``), management API and UDP discovery.

Lets Alpaca clients (N.I.N.A., Stellarium, ASCOM Remote, alpyca scripts, ...) read the mount position and send
GoTo / Sync / Tracking / AbortSlew commands. Implements ITelescopeV4 for an alt-az mount.

Integration with the rest of the server (see docs/alpaca.md):

* Each Alpaca client (remote address + ``ClientID``) that sets ``Connected=true`` gets an ordinary session in the
  :class:`~.sessions.SessionManager`, so it shows up in the web UI's client list.
* Motion commands obey the normal control authority: the Alpaca client takes control automatically **only if no
  other client holds it**; otherwise the command fails with ``InvalidOperationException``. A browser can take control
  back (force) at any time.
* ``AbortSlew`` is a stop and, like Stop/E-stop in the UI, is accepted from every connected client.
* Every command goes through :meth:`Observatory.execute` and :class:`MountController`, so the solar-safety policy,
  altitude/cable-wrap limits and fault handling are the same as for the web UI.

Deliberate deviations for an alt-az mount whose tracking is tied to a target:
``SlewToCoordinatesAsync``/``SyncToCoordinates`` do not require ``Tracking=true`` (a coordinate slew always ends in
tracking; from the park position, at the horizon, tracking cannot be switched on first). ``MoveAxis``, pulse guiding,
park and find-home are not implemented yet (``Can*`` report False).
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
import logging
import math
import socket
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse

from .. import __version__
from ..mount.controller import CommandError, Mode

if TYPE_CHECKING:
    from .app import Observatory

log = logging.getLogger("openskymount.alpaca")

DISCOVERY_PORT = 32227
DISCOVERY_MESSAGE = b"alpacadiscovery1"
INTERFACE_VERSION = 4
CLIENT_IDLE_S = 300.0  # an Alpaca client silent for this long is disconnected

# ASCOM error numbers (Alpaca API reference).
NOT_IMPLEMENTED = 0x400
INVALID_VALUE = 0x401
VALUE_NOT_SET = 0x402
NOT_CONNECTED = 0x407
INVALID_OPERATION = 0x40B
ACTION_NOT_IMPLEMENTED = 0x40C
DRIVER_ERROR = 0x500

# Readable without Connected=true.
COMMON = frozenset({"connected", "connecting", "connect", "disconnect", "description", "driverinfo", "driverversion",
                    "interfaceversion", "name", "supportedactions"})

UNSUPPORTED_GET = frozenset({"aperturearea", "aperturediameter", "focallength", "guideratedeclination",
                             "guideraterightascension", "ispulseguiding", "sideofpier", "destinationsideofpier"})
UNSUPPORTED_PUT = frozenset({"action", "commandblind", "commandbool", "commandstring", "declinationrate", "doesrefraction",
                             "findhome", "guideratedeclination", "guideraterightascension", "moveaxis", "park",
                             "pulseguide", "rightascensionrate", "setpark", "sideofpier", "slewsettletime",
                             "slewtoaltaz", "slewtocoordinates", "slewtotarget", "synctoaltaz", "unpark", "utcdate"})

CAPABILITIES = {
    "canfindhome": False, "canpark": False, "canpulseguide": False, "cansetdeclinationrate": False,
    "cansetguiderates": False, "cansetpark": False, "cansetpierside": False, "cansetrightascensionrate": False,
    "cansettracking": True, "canslew": False, "canslewasync": True, "canslewaltaz": False, "canslewaltazasync": True,
    "cansync": True, "cansyncaltaz": False, "canunpark": False,
}


class AlpacaError(Exception):
    def __init__(self, number: int, message: str) -> None:
        super().__init__(message)
        self.number = number
        self.message = message


class BadRequest(Exception):
    """Malformed request (missing or unparsable parameter): HTTP 400 per the Alpaca spec."""


@dataclass
class AlpacaClient:
    key: tuple[str, int]
    session_id: str
    last_seen: float


class Params:
    """Alpaca parameters: names are case-insensitive, values are not."""

    def __init__(self, items: dict[str, list[str]]) -> None:
        self._d = {k.lower(): v[0] for k, v in items.items() if v}

    def raw(self, name: str) -> str:
        if name.lower() not in self._d:
            raise BadRequest(f"missing parameter {name}")
        return self._d[name.lower()]

    def uint(self, name: str) -> int:
        """ClientID / ClientTransactionID: unsigned 32-bit, 0 when missing or invalid."""
        try:
            v = int(self._d.get(name.lower(), "0"))
        except ValueError:
            return 0
        return v if 0 <= v <= 0xFFFFFFFF else 0

    def boolean(self, name: str) -> bool:
        v = self.raw(name).strip().lower()
        if v not in ("true", "false"):
            raise BadRequest(f"{name} must be true or false")
        return v == "true"

    def number(self, name: str) -> float:
        try:
            v = float(self.raw(name))
        except ValueError as exc:
            raise BadRequest(f"{name} must be a number") from exc
        if not math.isfinite(v):
            raise AlpacaError(INVALID_VALUE, f"{name} must be finite")
        return v

    def integer(self, name: str) -> int:
        try:
            return int(self.raw(name))
        except ValueError as exc:
            raise BadRequest(f"{name} must be an integer") from exc


class AlpacaTelescope:
    """Protocol-independent logic of telescope device 0 (called on the event-loop thread)."""

    def __init__(self, observatory: Callable[[], Observatory], unique_id: str) -> None:
        self._obs = observatory
        self.unique_id = unique_id
        self.clients: dict[tuple[str, int], AlpacaClient] = {}
        self.target_ra: float | None = None  # hours, JNow
        self.target_dec: float | None = None
        self._tracking_before_slew = False
        self._slew_target: object | None = None  # the controller target of our last coordinate slew

    @property
    def o(self) -> Observatory:
        return self._obs()

    # --------------------------------------------------------------- clients
    def client(self, key: tuple[str, int], now: float) -> AlpacaClient | None:
        c = self.clients.get(key)
        if c is not None:
            if c.session_id not in self.o.sessions.sessions:  # removed elsewhere
                del self.clients[key]
                return None
            c.last_seen = now
            self.o.sessions.touch(c.session_id, now)
        return c

    def connect(self, key: tuple[str, int], now: float) -> None:
        if self.client(key, now) is not None:
            return
        host, cid = key
        s = self.o.sessions.open(f"Alpaca #{cid} @{host}", now)
        s.meta["alpaca"] = True
        self.clients[key] = AlpacaClient(key, s.id, now)
        self.o.controller.log(f"Alpaca client connected ({host}, ClientID {cid})")
        self.o.broadcast({"type": "control", **self.o.sessions.describe(now)})

    def disconnect(self, key: tuple[str, int], reason: str = "Alpaca client disconnected") -> None:
        c = self.clients.pop(key, None)
        if c is None:
            return
        if self.o.sessions.close(c.session_id):
            self.o._on_controller_change(reason)
        else:
            self.o.broadcast({"type": "control", **self.o.sessions.describe(time.time())})

    def purge(self, now: float) -> None:
        for key, c in list(self.clients.items()):
            if now - c.last_seen > CLIENT_IDLE_S:
                self.disconnect(key, "Alpaca client idle")

    def _take_control(self, c: AlpacaClient, now: float) -> None:
        s = self.o.sessions
        if s.is_controller(c.session_id, now):
            return
        granted, prev = s.request_control(c.session_id, now, force=False)
        if not granted:
            who = s.describe(now).get("controller_name") or "another client"
            raise AlpacaError(INVALID_OPERATION, f"{who} has control of the mount; release it in the OpenSkyMount UI")
        if prev != c.session_id:
            self.o._on_controller_change("control taken by an Alpaca client")

    def _execute(self, c: AlpacaClient, cmd: str, **args: Any) -> dict[str, Any]:
        now = time.time()
        self._take_control(c, now)
        try:
            res = self.o.execute(c.session_id, cmd, args)
        except CommandError as exc:
            raise AlpacaError(INVALID_OPERATION, str(exc)) from exc
        if not res.get("ok", False):
            raise AlpacaError(INVALID_OPERATION, str(res.get("message", "command refused")))
        return res

    # ------------------------------------------------------------ state reads
    def _mode(self) -> str:
        return self.o.controller.mode

    def tracking(self) -> bool:
        ctl = self.o.controller
        return ctl.mode == Mode.TRACKING or (ctl.mode == Mode.SLEWING and ctl.track_after_slew)

    def slewing(self) -> bool:
        return self._mode() in (Mode.SLEWING, Mode.MANUAL, Mode.HOMING)

    def altaz(self) -> tuple[float, float]:
        alt, az = self.o.controller.pointing()
        return alt, az % 360.0

    def radec_now(self) -> tuple[float, float]:
        """Current pointing as apparent topocentric RA (hours) / Dec (deg) of date."""
        ctl = self.o.controller
        alt, az = ctl.pointing()
        rd = self.o.engine.altaz_to_radec(alt, az, ctl.sim_time)
        return (rd["ra_date"] % 360.0) / 15.0, rd["dec_date"]

    def sidereal_time(self) -> float:
        return self.o.engine.local_sidereal_time_deg(self.o.clock.now()) / 15.0

    def utc_date(self) -> str:
        t = dt.datetime.fromtimestamp(self.o.clock.now(), tz=dt.UTC)
        return t.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"

    def tracking_rate(self) -> int:
        target = self.o.controller.target
        if target is not None and target.kind == "moon":
            return 1  # driveLunar
        if target is not None and target.kind == "sun":
            return 2  # driveSolar
        return 0  # driveSidereal

    def device_state(self) -> list[dict[str, Any]]:
        ra, dec = self.radec_now()
        alt, az = self.altaz()
        values = {
            "Altitude": alt, "AtHome": False, "AtPark": False, "Azimuth": az, "Declination": dec,
            "RightAscension": ra, "SiderealTime": self.sidereal_time(), "Slewing": self.slewing(),
            "Tracking": self.tracking(), "UTCDate": self.utc_date(),
            "TimeStamp": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z",
        }
        return [{"Name": k, "Value": v} for k, v in values.items()]

    # ------------------------------------------------------------- commands
    @staticmethod
    def _check_radec(ra_h: float, dec: float) -> None:
        if not 0.0 <= ra_h < 24.0:
            raise AlpacaError(INVALID_VALUE, f"RightAscension {ra_h} outside [0, 24) hours")
        if not -90.0 <= dec <= 90.0:
            raise AlpacaError(INVALID_VALUE, f"Declination {dec} outside [-90, 90] degrees")

    def _icrs_target(self, ra_h: float, dec: float) -> str:
        ra_j, dec_j = self.o.engine.apparent_to_icrs(ra_h * 15.0, dec, self.o.clock.now())
        return f"radec:{ra_j:.6f},{dec_j:.6f}"

    def slew_to_coordinates(self, c: AlpacaClient, ra_h: float, dec: float) -> None:
        self._check_radec(ra_h, dec)
        self.target_ra, self.target_dec = ra_h, dec
        was_tracking = self.tracking()
        self._execute(c, "goto", target=self._icrs_target(ra_h, dec), track=True)
        self._tracking_before_slew = was_tracking
        self._slew_target = self.o.controller.target

    def slew_to_target(self, c: AlpacaClient) -> None:
        if self.target_ra is None or self.target_dec is None:
            raise AlpacaError(INVALID_OPERATION, "TargetRightAscension and TargetDeclination are not set")
        self.slew_to_coordinates(c, self.target_ra, self.target_dec)

    def slew_to_altaz(self, c: AlpacaClient, az: float, alt: float) -> None:
        if not 0.0 <= az < 360.0:
            raise AlpacaError(INVALID_VALUE, f"Azimuth {az} outside [0, 360)")
        if not -90.0 <= alt <= 90.0:
            raise AlpacaError(INVALID_VALUE, f"Altitude {alt} outside [-90, 90]")
        if self.tracking():
            raise AlpacaError(INVALID_OPERATION, "set Tracking=false before an alt/az slew")
        self._execute(c, "goto", target=f"altaz:{alt:.6f},{az:.6f}", track=False)
        self._slew_target = None

    def sync_to_coordinates(self, c: AlpacaClient, ra_h: float, dec: float) -> None:
        self._check_radec(ra_h, dec)
        self.target_ra, self.target_dec = ra_h, dec
        self._execute(c, "sync", target=self._icrs_target(ra_h, dec))

    def sync_to_target(self, c: AlpacaClient) -> None:
        if self.target_ra is None or self.target_dec is None:
            raise AlpacaError(INVALID_OPERATION, "TargetRightAscension and TargetDeclination are not set")
        self.sync_to_coordinates(c, self.target_ra, self.target_dec)

    def set_tracking(self, c: AlpacaClient, on: bool) -> None:
        if on == self.tracking():
            return
        if not on:
            self._execute(c, "track_stop")
            return
        # Track the sky position the mount points at now.
        ra_h, dec = self.radec_now()
        self._execute(c, "goto", target=self._icrs_target(ra_h, dec), track=True)

    def abort_slew(self, c: AlpacaClient) -> None:
        """Stop a slew / manual move. Accepted from every client (safety); tracking returns to its pre-slew state."""
        if not self.slewing():
            return
        # Only restore tracking for a slew this device started (not, e.g., a GoTo from the web UI).
        resume = (self._mode() == Mode.SLEWING and self._tracking_before_slew
                  and self.o.controller.target is self._slew_target)
        self.o.controller.log("AbortSlew from an Alpaca client")
        self.o.execute(c.session_id, "stop", {})
        if resume:
            try:
                self.set_tracking(c, True)
            except AlpacaError as exc:
                self.o.controller.log(f"Tracking not resumed after AbortSlew: {exc.message}", "warning")

    def set_site(self, c: AlpacaClient, field: str, value: float) -> None:
        self._execute(c, "set_config", observer={field: value})


# ---------------------------------------------------------------------------- HTTP layer


def _respond(params: Params, server_tid: int, value: Any = None, has_value: bool = True,
             error: AlpacaError | None = None) -> JSONResponse:
    body: dict[str, Any] = {
        "ClientTransactionID": params.uint("ClientTransactionID"),
        "ServerTransactionID": server_tid,
        "ErrorNumber": error.number if error else 0,
        "ErrorMessage": error.message if error else "",
    }
    if has_value and error is None:
        body["Value"] = value
    return JSONResponse(body)


def register_alpaca(app: FastAPI, observatory: Callable[[], Observatory], unique_id: str) -> AlpacaTelescope:
    dev = AlpacaTelescope(observatory, unique_id)
    counter = {"tid": 0}

    def next_tid() -> int:
        counter["tid"] = (counter["tid"] % 0xFFFFFFFF) + 1
        return counter["tid"]

    def mgmt(value: Any, request: Request) -> JSONResponse:
        return _respond(Params(parse_qs(request.url.query)), next_tid(), value)

    @app.get("/management/apiversions")
    async def api_versions(request: Request) -> JSONResponse:
        return mgmt([1], request)

    @app.get("/management/v1/description")
    async def description(request: Request) -> JSONResponse:
        return mgmt({"ServerName": "OpenSkyMount", "Manufacturer": "OpenSkyMount project",
                     "ManufacturerVersion": __version__, "Location": dev.o.config.observer.name}, request)

    @app.get("/management/v1/configureddevices")
    async def configured_devices(request: Request) -> JSONResponse:
        return mgmt([{"DeviceName": f"OpenSkyMount ({dev.o.driver.capabilities().name})", "DeviceType": "Telescope",
                      "DeviceNumber": 0, "UniqueID": dev.unique_id}], request)

    @app.get("/setup")
    @app.get("/setup/v1/telescope/0/setup")
    async def setup_page() -> RedirectResponse:
        return RedirectResponse("/")

    # async handlers: they run on the event-loop thread, like the control loop (no data races).
    @app.get("/api/v1/telescope/{number}/{member}")
    async def alpaca_get(number: str, member: str, request: Request) -> Any:
        return handle(number, member, request, Params(parse_qs(request.url.query, keep_blank_values=True)), False)

    @app.put("/api/v1/telescope/{number}/{member}")
    async def alpaca_put(number: str, member: str, request: Request) -> Any:
        body = (await request.body()).decode("utf-8", errors="replace")
        return handle(number, member, request, Params(parse_qs(body, keep_blank_values=True)), True)

    def handle(number: str, member: str, request: Request, p: Params, put: bool) -> Any:
        if number != "0":
            return PlainTextResponse(f"no telescope device number {number}", status_code=404)
        table = PUT if put else GET
        if member not in table and member not in (UNSUPPORTED_PUT if put else UNSUPPORTED_GET):
            return PlainTextResponse(f"unknown {'method' if put else 'property'} {member!r}", status_code=400)
        tid = next_tid()
        now = time.time()
        key = (request.client.host if request.client else "?", p.uint("ClientID"))
        try:
            client = dev.client(key, now)
            if member in (UNSUPPORTED_PUT if put else UNSUPPORTED_GET):
                if member == "action":
                    raise AlpacaError(ACTION_NOT_IMPLEMENTED, "no actions are supported")
                if client is None and member not in COMMON:
                    raise AlpacaError(NOT_CONNECTED, "not connected (set Connected=true)")
                raise AlpacaError(NOT_IMPLEMENTED, f"{member} is not implemented")
            if client is None and member not in COMMON:
                raise AlpacaError(NOT_CONNECTED, "not connected (set Connected=true)")
            fn = table[member]
            value = fn(key, client, p)
            return _respond(p, tid, value, has_value=not put or value is not None)
        except BadRequest as exc:
            return PlainTextResponse(str(exc), status_code=400)
        except AlpacaError as exc:
            return _respond(p, tid, error=exc)
        except Exception as exc:  # pragma: no cover - surface unexpected failures as driver errors
            log.exception("Alpaca %s %s failed", "PUT" if put else "GET", member)
            return _respond(p, tid, error=AlpacaError(DRIVER_ERROR, f"internal error: {exc}"))

    def need(c: AlpacaClient | None) -> AlpacaClient:
        assert c is not None  # guaranteed by the NOT_CONNECTED check
        return c

    def get_static(value: Any) -> Callable[..., Any]:
        return lambda key, c, p: value

    def site_put(field: str, param: str) -> Callable[..., Any]:
        def fn(key: tuple[str, int], c: AlpacaClient | None, p: Params) -> None:
            v = p.number(param)
            try:
                dev.set_site(need(c), field, v)
            except AlpacaError as exc:
                if "must be within" in exc.message:
                    raise AlpacaError(INVALID_VALUE, exc.message) from exc
                raise

        return fn

    def axis_param(p: Params) -> int:
        axis = p.integer("Axis")
        if axis not in (0, 1, 2):
            raise AlpacaError(INVALID_VALUE, f"Axis {axis} must be 0, 1 or 2")
        return axis

    def set_tracking_rate(key: tuple[str, int], c: AlpacaClient | None, p: Params) -> None:
        rate = p.integer("TrackingRate")
        if rate != 0:
            raise AlpacaError(INVALID_VALUE, "only driveSidereal (0) is supported")

    def set_connected(key: tuple[str, int], c: AlpacaClient | None, p: Params) -> None:
        if p.boolean("Connected"):
            dev.connect(key, time.time())
        else:
            dev.disconnect(key)

    def set_target(attr: str, param: str, lo: float, hi: float, hi_open: bool) -> Callable[..., Any]:
        def fn(key: tuple[str, int], c: AlpacaClient | None, p: Params) -> None:
            v = p.number(param)
            if not (lo <= v < hi if hi_open else lo <= v <= hi):
                raise AlpacaError(INVALID_VALUE, f"{param} {v} out of range")
            setattr(dev, attr, v)

        return fn

    def get_target(attr: str) -> Callable[..., Any]:
        def fn(key: tuple[str, int], c: AlpacaClient | None, p: Params) -> float:
            v = getattr(dev, attr)
            if v is None:
                raise AlpacaError(VALUE_NOT_SET, f"{attr} has not been set")
            return float(v)

        return fn

    GET: dict[str, Callable[[tuple[str, int], AlpacaClient | None, Params], Any]] = {
        # Common device members
        "connected": lambda key, c, p: c is not None,
        "connecting": get_static(False),
        "description": lambda key, c, p: "OpenSkyMount alt-az mount"
        + (" (simulated)" if dev.o.driver.capabilities().simulated else ""),
        "driverinfo": get_static(f"OpenSkyMount {__version__} ASCOM Alpaca Telescope (ITelescopeV4), "
                                 "https://github.com/skynightgamer/openskymount"),
        "driverversion": get_static(__version__),
        "interfaceversion": get_static(INTERFACE_VERSION),
        "name": get_static("OpenSkyMount"),
        "supportedactions": get_static([]),
        "devicestate": lambda key, c, p: dev.device_state(),
        # Telescope
        "alignmentmode": get_static(0),  # algAltAz
        "altitude": lambda key, c, p: dev.altaz()[0],
        "azimuth": lambda key, c, p: dev.altaz()[1],
        "athome": get_static(False),
        "atpark": get_static(False),
        "rightascension": lambda key, c, p: dev.radec_now()[0],
        "declination": lambda key, c, p: dev.radec_now()[1],
        "rightascensionrate": get_static(0.0),
        "declinationrate": get_static(0.0),
        "doesrefraction": lambda key, c, p: dev.o.engine._refraction_kwargs() != {},
        "equatorialsystem": get_static(1),  # equTopocentric ("JNow")
        "siderealtime": lambda key, c, p: dev.sidereal_time(),
        "siteelevation": lambda key, c, p: dev.o.config.observer.elevation_m,
        "sitelatitude": lambda key, c, p: dev.o.config.observer.latitude,
        "sitelongitude": lambda key, c, p: dev.o.config.observer.longitude,
        "slewing": lambda key, c, p: dev.slewing(),
        "slewsettletime": get_static(0),
        "targetrightascension": get_target("target_ra"),
        "targetdeclination": get_target("target_dec"),
        "tracking": lambda key, c, p: dev.tracking(),
        "trackingrate": lambda key, c, p: dev.tracking_rate(),
        "trackingrates": get_static([0]),
        "utcdate": lambda key, c, p: dev.utc_date(),
        "axisrates": lambda key, c, p: (axis_param(p), [])[1],
        "canmoveaxis": lambda key, c, p: (axis_param(p), False)[1],
        **{name: get_static(v) for name, v in CAPABILITIES.items()},
    }

    PUT: dict[str, Callable[[tuple[str, int], AlpacaClient | None, Params], Any]] = {
        "connected": set_connected,
        "connect": lambda key, c, p: dev.connect(key, time.time()),
        "disconnect": lambda key, c, p: dev.disconnect(key),
        "abortslew": lambda key, c, p: dev.abort_slew(need(c)),
        "slewtocoordinatesasync": lambda key, c, p: dev.slew_to_coordinates(
            need(c), p.number("RightAscension"), p.number("Declination")),
        "slewtotargetasync": lambda key, c, p: dev.slew_to_target(need(c)),
        "slewtoaltazasync": lambda key, c, p: dev.slew_to_altaz(need(c), p.number("Azimuth"), p.number("Altitude")),
        "synctocoordinates": lambda key, c, p: dev.sync_to_coordinates(
            need(c), p.number("RightAscension"), p.number("Declination")),
        "synctotarget": lambda key, c, p: dev.sync_to_target(need(c)),
        "tracking": lambda key, c, p: dev.set_tracking(need(c), p.boolean("Tracking")),
        "trackingrate": set_tracking_rate,
        "targetrightascension": set_target("target_ra", "TargetRightAscension", 0.0, 24.0, True),
        "targetdeclination": set_target("target_dec", "TargetDeclination", -90.0, 90.0, False),
        "siteelevation": site_put("elevation_m", "SiteElevation"),
        "sitelatitude": site_put("latitude", "SiteLatitude"),
        "sitelongitude": site_put("longitude", "SiteLongitude"),
    }
    return dev


def unique_id_for(state_dir: str | None) -> str:
    """Stable per installation (derived from the state directory), as Alpaca requires."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"openskymount:{state_dir or 'default'}"))


# ---------------------------------------------------------------------------- discovery


class _DiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(self, http_port: int) -> None:
        self.reply = json.dumps({"AlpacaPort": http_port}).encode()
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str | Any, int]) -> None:
        if data.startswith(DISCOVERY_MESSAGE) and self.transport is not None:
            self.transport.sendto(self.reply, addr)


async def start_discovery(bind_host: str, http_port: int, udp_port: int = DISCOVERY_PORT) -> asyncio.DatagramTransport | None:
    """Answer Alpaca discovery broadcasts (IPv4). Returns None (and logs) if the port cannot be bound."""
    loop = asyncio.get_running_loop()
    host = "" if bind_host in ("0.0.0.0", "") else bind_host
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        with contextlib.suppress(AttributeError, OSError):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)  # several Alpaca servers on one host
        sock.bind((host, udp_port))
    except OSError as exc:
        sock.close()
        log.warning("Alpaca discovery disabled: cannot bind UDP port %s (%s)", udp_port, exc)
        return None
    transport, _ = await loop.create_datagram_endpoint(lambda: _DiscoveryProtocol(http_port), sock=sock)
    return transport
