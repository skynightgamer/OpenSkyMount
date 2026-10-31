"""API and WebSocket synchronisation tests (FastAPI TestClient, real event loop)."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from openskymount.server.app import Observatory, create_app

from .conftest import T0


@pytest.fixture
def client(tmp_path) -> Iterator[TestClient]:
    obs = Observatory(state_dir=tmp_path)
    with TestClient(create_app(observatory=obs)) as c:
        yield c


def recv_until(ws, pred, timeout: float = 10.0) -> dict[str, Any]:
    end = time.time() + timeout
    while time.time() < end:
        msg = json.loads(ws.receive_text())
        if pred(msg):
            return msg
    raise AssertionError("expected message not received")


class Cmd:
    def __init__(self, ws) -> None:
        self.ws = ws
        self.seq = 0

    def __call__(self, cmd: str, **args: Any) -> dict[str, Any]:
        self.seq += 1
        seq = self.seq
        self.ws.send_text(json.dumps({"type": "command", "seq": seq, "cmd": cmd, "args": args}))
        return recv_until(self.ws, lambda m: m.get("type") == "ack" and m.get("seq") == seq)

    def control(self, force: bool = False) -> dict[str, Any]:
        self.ws.send_text(json.dumps({"type": "request_control", "force": force}))
        return recv_until(self.ws, lambda m: m.get("type") == "control_result")


def test_rest_endpoints(client: TestClient) -> None:
    assert client.get("/api/health").json()["ok"]
    cat = client.get("/api/catalog").json()
    assert len(cat["stars"]) > 4000 and len(cat["constellations"]) == 89
    sky = client.get("/api/sky", params={"t": T0}).json()
    assert len(sky["matrix"]) == 3 and {b["name"] for b in sky["bodies"]} >= {"Sun", "Moon", "Jupiter"}
    hits = client.get("/api/search", params={"q": "jup"}).json()
    assert hits[0]["id"] == "planet:jupiter"
    info = client.get("/api/object", params={"id": "planet:jupiter", "t": T0}).json()
    assert info["above_horizon"] and 0 <= info["az"] < 360
    assert client.get("/api/object", params={"id": "nothing-here"}).status_code == 404
    assert client.get("/api/search", params={"q": ""}).status_code == 422
    cfg = client.get("/api/config").json()
    assert "smartphone" in cfg["payloads"] and cfg["capabilities"]["simulated"]
    st = client.get("/api/status").json()
    assert st["mount"]["mode"] == "idle"


def test_control_authority_and_safety(client: TestClient) -> None:
    with client.websocket_connect("/ws?name=A") as a, client.websocket_connect("/ws?name=B") as b:
        ca, cb = Cmd(a), Cmd(b)
        recv_until(a, lambda m: m["type"] == "welcome")
        recv_until(b, lambda m: m["type"] == "welcome")
        # Observers cannot move the mount.
        r = cb("goto", target="Capella")
        assert not r["ok"] and "control" in r["message"]
        assert ca.control()["granted"]
        assert not cb.control()["granted"]  # A holds an active lease
        # Emergency stop is always accepted, even from an observer.
        assert cb("estop")["ok"]
        recv_until(a, lambda m: m["type"] == "state" and m["mount"]["mode"] == "emergency_stop")
        r = ca("goto", target="Capella")
        assert not r["ok"] and "emergency" in r["message"]
        assert ca("reset")["ok"]
        # Replay protection.
        a.send_text(json.dumps({"type": "command", "seq": 1, "cmd": "home", "args": {}}))
        r = recv_until(a, lambda m: m.get("type") == "ack" and m.get("seq") == 1)
        assert not r["ok"] and "stale" in r["message"]
        # Forced takeover.
        assert cb.control(force=True)["granted"]
        r = ca("home")
        assert not r["ok"]
        # Bad input.
        a.send_text("{not json")
        assert recv_until(a, lambda m: m["type"] == "error")["message"] == "invalid JSON message"
        r = cb("set_config", observer={"latitude": 123})
        assert not r["ok"] and "latitude" in r["message"]
        r = cb("manual", alt_rate="fast")
        assert not r["ok"]
        r = cb("no_such_command")
        assert not r["ok"]


def test_two_clients_see_same_goto(client: TestClient) -> None:
    with client.websocket_connect("/ws?name=phone") as a, client.websocket_connect("/ws?name=laptop") as b:
        ca = Cmd(a)
        recv_until(b, lambda m: m["type"] == "welcome")
        assert ca.control()["granted"]
        assert ca("set_clock", time=T0, rate=10)["ok"]
        r = ca("goto", target="jupiter")
        assert r["ok"], r
        # The observer window sees the slew and then tracking of the same target.
        m = recv_until(b, lambda m: m["type"] == "state" and m["mount"]["mode"] == "slewing")
        assert m["mount"]["target"]["name"] == "Jupiter"
        assert m["control"]["controller_name"] == "phone"
        m = recv_until(b, lambda m: m["type"] == "state" and m["mount"]["mode"] == "tracking", timeout=30)
        assert m["mount"]["tracking_error"] < 0.05
        assert not m["clock"]["live"] and m["clock"]["rate"] == 10
        # Both clients receive the same authoritative state stream.
        sa = recv_until(a, lambda m: m["type"] == "state")
        assert sa["mount"]["target"]["id"] == "planet:jupiter"


def test_controller_disconnect_stops_manual_motion(client: TestClient) -> None:
    with client.websocket_connect("/ws?name=observer") as b:
        with client.websocket_connect("/ws?name=driver") as a:
            ca = Cmd(a)
            assert ca.control()["granted"]
            assert ca("manual", alt_rate=2.0, az_rate=0.0)["ok"]
            recv_until(b, lambda m: m["type"] == "state" and m["mount"]["mode"] == "manual")
        # Controller window closed: control released, manual slew stopped.
        m = recv_until(b, lambda m: m["type"] == "state" and m["mount"]["mode"] == "idle")
        assert m["control"]["controller"] is None


def test_config_change_persists(tmp_path) -> None:
    obs = Observatory(state_dir=tmp_path)
    with TestClient(create_app(observatory=obs)) as c, c.websocket_connect("/ws?name=a") as a:
        ca = Cmd(a)
        ca.control()
        r = ca("set_config", observer={"latitude": -33.87, "longitude": 151.21, "name": "Sydney"}, payload="telescope")
        assert r["ok"], r
        recv_until(a, lambda m: m["type"] == "config" and m["config"]["observer"]["name"] == "Sydney")
    again = Observatory(state_dir=tmp_path)
    assert again.config.observer.name == "Sydney" and again.config.payload == "telescope"
    assert again.controller.limits.max_altitude == 82.0
