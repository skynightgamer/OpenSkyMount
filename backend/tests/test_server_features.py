"""REST/WebSocket API of the orbit store, observing session queue and telemetry recording."""

from __future__ import annotations

import datetime as dt
import json
import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from openskymount.clock import ManualClock
from openskymount.server.app import Observatory, create_app

from .test_orbits import ISS, comet_line
from .test_server import Cmd, recv_until

T_ISS = dt.datetime(2014, 1, 21, 3, 0, tzinfo=dt.UTC).timestamp()


@pytest.fixture
def obs(tmp_path) -> Observatory:
    return Observatory(state_dir=tmp_path, clock=ManualClock(T_ISS))


@pytest.fixture
def client(obs: Observatory) -> Iterator[TestClient]:
    with TestClient(create_app(observatory=obs)) as c:
        yield c


def test_orbit_upload_search_passes_and_removal(client: TestClient, obs: Observatory) -> None:
    assert client.get("/api/orbits").json()["objects"] == []
    r = client.post("/api/orbits", content=ISS, headers={"content-type": "text/plain"})
    assert r.status_code == 200 and r.json()["added"] == ["sat:25544"]
    encke = comet_line(1990, 10, 28.54502, 0.330886, 0.850220, 186.23352, 334.75006, 11.94524, 11.5, 6.0, "2P/Encke",
                       number="0002")
    r = client.post("/api/orbits", json={"text": encke})
    assert r.json()["added"] == ["comet:2P/Encke"]
    bad = client.post("/api/orbits", json={"text": "ISS\n1 25544U nonsense\n2 25544 nonsense"})
    assert bad.status_code == 400
    objs = client.get("/api/orbits").json()["objects"]
    assert {o["kind"] for o in objs} == {"satellite", "comet"}
    assert next(o for o in objs if o["kind"] == "satellite")["age_days"] == pytest.approx(0.19, abs=0.05)
    assert client.get("/api/search", params={"q": "ISS"}).json()[0]["id"] == "sat:25544"
    info = client.get("/api/object", params={"id": "sat:25544", "t": T_ISS}).json()
    assert info["kind"] == "satellite" and "sunlit" in info
    passes = client.get("/api/passes", params={"id": "sat:25544", "t": T_ISS, "hours": 6}).json()["passes"]
    assert passes and passes[0]["rise"] < passes[0]["culminate"] < passes[0]["set"]
    assert client.get("/api/passes", params={"id": "planet:mars"}).status_code == 404
    sky = client.get("/api/sky", params={"t": passes[0]["culminate"]}).json()
    assert any(b["id"] == "sat:25544" for b in sky["bodies"])
    # Elements survive a restart (state/orbits.json).
    assert len(Observatory(state_dir=obs.state_dir, clock=ManualClock(T_ISS)).orbits) == 2
    assert client.delete("/api/orbits", params={"id": "comet:2P/Encke"}).status_code == 200
    assert client.delete("/api/orbits", params={"id": "comet:2P/Encke"}).status_code == 409
    assert client.delete("/api/orbits").status_code == 200
    assert client.get("/api/orbits").json()["objects"] == []


def test_queue_and_recording_over_websocket(client: TestClient, obs: Observatory) -> None:
    clock = obs.clock
    assert isinstance(clock, ManualClock)
    with client.websocket_connect("/ws?name=A") as ws:
        cmd = Cmd(ws)
        recv_until(ws, lambda m: m["type"] == "welcome")
        r = cmd("queue_add", target="Capella", duration_s=30)
        assert not r["ok"] and "control" in r["message"]
        assert cmd.control()["granted"]
        assert cmd("queue_add", target="Capella", duration_s=30)["ok"]
        assert cmd("queue_add", target="Arcturus", duration_s=30)["ok"]
        assert not cmd("queue_add", target="sun")["ok"]
        assert cmd("queue_start", record=True)["ok"]
        # The server's own 20 Hz loop advances the controller and session and records at 5 Hz (wall time).
        end = time.time() + 20
        st: dict = {}
        while time.time() < end:
            clock.advance(2.0)
            time.sleep(0.05)
            st = recv_until(ws, lambda m: m["type"] == "state")
            if st["session"]["items"][1]["status"] == "done":
                break
        assert [it["status"] for it in st["session"]["items"]] == ["done", "done"], st["session"]
        assert not st["session"]["running"] and st["recording"]["active"] is False
        # The recording started with the session stopped when the session completed.
        name = obs.recorder.name
        assert cmd("record_stop")["name"] is None
    logs = client.get("/api/logs").json()["logs"]
    assert [lg["name"] for lg in logs] == [name]
    text = client.get(f"/api/logs/{name}").text
    lines = [json.loads(ln) for ln in text.splitlines()]
    assert lines[0]["k"] == "h" and lines[0]["format"] == "openskymount-telemetry/1"
    samples = [ln for ln in lines if ln["k"] == "s"]
    assert len(samples) > 10 and {"slewing", "tracking"} <= {s["m"] for s in samples}
    assert any(ln["k"] == "e" and "GoTo" in ln["msg"] for ln in lines)
    assert client.get("/api/logs/..%2Fconfig.json").status_code == 404
    assert client.delete(f"/api/logs/{name}").status_code == 200
    assert client.get("/api/logs").json()["logs"] == []
