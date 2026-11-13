"""Mosaic and timelapse planning: geometry checked against spherical trigonometry, field rotation against the parallactic angle."""

from __future__ import annotations

import math

import numpy as np
import pytest
from fastapi.testclient import TestClient

from openskymount.config import AppConfig
from openskymount.mount.controller import CommandError
from openskymount.mount.planning import MAX_PANELS, mosaic_grid, mosaic_plan, timelapse_plan
from openskymount.mount.sequencer import Sequencer
from openskymount.server.app import Observatory, create_app

from .conftest import T0, Rig


def angle(a: dict, b: dict) -> float:
    """Angle (degrees) between two panel centres."""
    def vec(p: dict) -> np.ndarray:
        r, d = math.radians(p["ra"]), math.radians(p["dec"])
        return np.array([math.cos(d) * math.cos(r), math.cos(d) * math.sin(r), math.sin(d)])

    return math.degrees(math.acos(max(-1.0, min(1.0, float(vec(a) @ vec(b))))))


# ---------------------------------------------------------------------------- mosaic geometry
def test_neighbouring_frames_on_the_centre_line_are_one_step_apart() -> None:
    panels = mosaic_grid(100.0, 0.0, cols=3, rows=1, fov_w=60.0, fov_h=45.0, overlap=0.25)
    step = 60.0 * 0.75
    assert angle(panels[0], panels[1]) == pytest.approx(step, abs=1e-6)
    assert angle(panels[1], panels[2]) == pytest.approx(step, abs=1e-6)
    column = mosaic_grid(100.0, 0.0, cols=1, rows=3, fov_w=60.0, fov_h=45.0, overlap=0.25)
    assert angle(column[0], column[1]) == pytest.approx(45.0 * 0.75, abs=1e-6)
    assert [p["dec"] for p in column] == sorted((p["dec"] for p in column), reverse=True)  # north row first


def test_frames_off_the_centre_line_are_spread_so_they_still_overlap_by_the_same_amount() -> None:
    panels = mosaic_grid(100.0, 0.0, cols=3, rows=3, fov_w=60.0, fov_h=45.0, overlap=0.25)
    top = [p for p in panels if p["row"] == 0]
    top.sort(key=lambda p: p["col"])
    # Along a parallel at 33.75 degrees the great-circle distance is a little under the step, but within 3 %.
    assert angle(top[0], top[1]) == pytest.approx(60.0 * 0.75, rel=0.03)
    assert top[1]["x"] - top[0]["x"] > 60.0 * 0.75  # longitude spacing is wider than the step


def test_the_middle_frame_of_an_odd_grid_is_the_centre() -> None:
    panels = mosaic_grid(201.5, 37.0, cols=5, rows=3, fov_w=20.0, fov_h=15.0, overlap=0.2)
    mid = next(p for p in panels if p["row"] == 1 and p["col"] == 2)
    assert (mid["ra"], mid["dec"]) == pytest.approx((201.5, 37.0), abs=1e-9)


def test_visiting_order_is_a_snake_from_the_west_and_every_step_is_a_neighbour() -> None:
    panels = mosaic_grid(100.0, 20.0, cols=4, rows=3, fov_w=30.0, fov_h=20.0, overlap=0.25)
    assert [p["index"] for p in panels] == list(range(12))
    assert (panels[0]["row"], panels[0]["col"]) == (0, 0)  # north-west corner first (west = smaller RA)
    assert panels[0]["x"] < panels[1]["x"]
    assert panels[4]["col"] == 3 and panels[7]["col"] == 0  # the second row runs back
    for a, b in zip(panels, panels[1:], strict=False):
        assert angle(a, b) < 1.25 * 30.0 * 0.75 / math.cos(math.radians(20.0))


def test_a_one_frame_mosaic_is_the_centre() -> None:
    (p,) = mosaic_grid(10.0, -30.0, 1, 1, 40.0, 30.0, 0.3)
    assert (p["ra"], p["dec"]) == pytest.approx((10.0, -30.0), abs=1e-9)


# ---------------------------------------------------------------------------- mosaic plan
def test_plan_reports_coverage_time_and_session_items() -> None:
    rig = Rig(AppConfig())
    plan = mosaic_plan(rig.ctl, "con:Cyg", T0 + 3 * 3600, 3, 2, 0.25, 40.0, 30.0, 30.0)
    assert len(plan["panels"]) == 6
    assert plan["coverage"] == {"w": pytest.approx(2 * 30.0 + 40.0), "h": pytest.approx(22.5 + 30.0)}
    assert plan["centre"]["name"] == "Cygnus (Swan)"
    longer = mosaic_plan(rig.ctl, "con:Cyg", T0 + 3 * 3600, 3, 2, 0.25, 40.0, 30.0, 90.0)
    assert longer["total_s"] - plan["total_s"] == pytest.approx(6 * 60.0)  # 60 s more on each of 6 frames
    for item, panel in zip(plan["items"], [p for p in plan["panels"] if p["reachable"]], strict=True):
        assert item["target"] == panel["target"] and item["duration_s"] == 30.0
        assert rig.engine.resolve(item["target"]).kind == "radec"
        assert item["label"].startswith("Cygnus")


def test_frames_below_the_horizon_or_next_to_the_sun_are_not_reachable() -> None:
    rig = Rig(AppConfig())
    low = rig.engine.altaz_to_radec(8.0, 180.0, T0)
    centre = f"radec:{low['ra_j2000']:.4f},{low['dec_j2000']:.4f}"
    plan = mosaic_plan(rig.ctl, centre, T0, 1, 3, 0.0, 30.0, 20.0, 10.0)
    flags = [p["reachable"] for p in plan["panels"]]
    assert flags.count(False) >= 1 and plan["unreachable"] == flags.count(False)
    bad = next(p for p in plan["panels"] if not p["reachable"])
    assert "below the altitude limit" in bad["problems"]
    assert len(plan["items"]) == flags.count(True)
    # The Sun: a frame centred on it is refused (the exclusion zone), at midday.
    noon = T0 + 12 * 3600
    sun = rig.engine.radec(rig.engine.resolve("sun"), noon)
    sunny = mosaic_plan(rig.ctl, f"radec:{sun['ra_j2000']:.4f},{sun['dec_j2000']:.4f}", noon, 1, 1, 0.0, 30.0, 20.0, 10.0)
    assert "too close to the Sun" in sunny["panels"][0]["problems"] and sunny["items"] == []


def test_plan_rejects_nonsense() -> None:
    rig = Rig(AppConfig())
    with pytest.raises(CommandError):
        mosaic_plan(rig.ctl, "con:Cyg", T0, 9, 9, 0.25, 30.0, 20.0, 10.0)  # 81 frames
    assert MAX_PANELS < 9 * 9
    with pytest.raises(CommandError):
        mosaic_plan(rig.ctl, "con:Cyg", T0, 2, 2, 0.9, 30.0, 20.0, 10.0)
    with pytest.raises(CommandError):
        mosaic_plan(rig.ctl, "altaz:30,100", T0, 2, 2, 0.2, 30.0, 20.0, 10.0)
    with pytest.raises(CommandError):
        mosaic_plan(rig.ctl, "no such object", T0, 2, 2, 0.2, 30.0, 20.0, 10.0)


# ---------------------------------------------------------------------------- queueing a whole mosaic
def test_add_many_queues_everything_or_nothing() -> None:
    rig = Rig(AppConfig())
    q = Sequencer(rig.ctl)
    items = [{"target": "con:Cyg", "duration_s": 30.0, "label": "Cygnus: frame 1/2"}, {"target": "radec:10,20", "duration_s": 15.0}]
    added = q.add_many(items, extra={"group": "mosaic"})
    assert [it.name for it in added][0] == "Cygnus: frame 1/2" and added[1].name.startswith("RA ")
    assert added[0].extra == {"group": "mosaic", "n": 1, "of": 2} and added[1].duration_s == 15.0
    before = len(q.items)
    with pytest.raises(CommandError):
        q.add_many([{"target": "con:Cyg"}, {"target": "no such object"}])
    with pytest.raises(CommandError):
        q.add_many([{"target": "con:Cyg"}, {"target": "sun"}])
    with pytest.raises(CommandError):
        q.add_many([{"target": "con:Cyg", "duration_s": -5}])
    assert len(q.items) == before
    nxt = q.add("con:Lyr")
    assert nxt.id == f"q{int(added[-1].id[1:]) + 1}", "failed batches must not use up item numbers"
    with pytest.raises(CommandError):
        q.add_many([])


# ---------------------------------------------------------------------------- timelapse
def parallactic_angle_deg(rig: Rig, target_id: str, t: float) -> float:
    """Parallactic angle from hour angle and declination alone (independent of the engine's rate formula)."""
    d = rig.engine.radec(rig.engine.resolve(target_id), t)
    h = math.radians(rig.engine.local_sidereal_time_deg(t) - d["ra_date"])
    dec = math.radians(d["dec_date"])
    lat = math.radians(rig.cfg.observer.latitude)
    return math.degrees(math.atan2(math.sin(h), math.tan(lat) * math.cos(dec) - math.sin(dec) * math.cos(h)))


def test_field_rotation_is_the_change_of_the_parallactic_angle() -> None:
    rig = Rig(AppConfig())
    for target, minutes in (("hip:24608", 60.0), ("hip:91262", 90.0), ("hip:11767", 120.0)):  # Capella, Vega, Polaris
        plan = timelapse_plan(rig.ctl, target, T0, minutes, 20.0)
        dq = parallactic_angle_deg(rig, target, T0 + minutes * 60) - parallactic_angle_deg(rig, target, T0)
        dq = (dq + 180.0) % 360.0 - 180.0
        assert plan["held_for_s"] == minutes * 60
        assert abs(plan["field_rotation_deg"]) == pytest.approx(abs(dq), rel=0.03, abs=0.05), target
    # Rotation is slow low in the east and fastest near the meridian: the same hour at two places differs a lot.
    east = timelapse_plan(rig.ctl, "planet:jupiter", T0, 60.0, 20.0)
    assert abs(east["field_rotation_deg"]) < 15.0


def test_frames_playback_and_star_motion() -> None:
    rig = Rig(AppConfig())
    plan = timelapse_plan(rig.ctl, "hip:24608", T0, 60.0, 10.0)
    assert plan["frames"] == 360 and plan["playback_s"] == pytest.approx(360 / 25.0)
    dec = rig.engine.radec(rig.engine.resolve("hip:24608"), T0)["dec_j2000"]
    assert plan["star_motion_per_frame_arcsec"] == pytest.approx(15.041 * 10.0 * math.cos(math.radians(dec)), rel=0.01)
    assert plan["field_rotation_per_frame_deg"] == pytest.approx(plan["field_rotation_deg"] / 360)
    assert plan["alt"]["min"] <= plan["alt"]["start"] <= plan["alt"]["max"] and plan["dark"]
    assert 0.0 <= plan["moon"]["illuminated"] <= 1.0 and plan["moon"]["separation_deg"] > 0.0


def test_a_target_that_sets_during_the_run_says_when() -> None:
    rig = Rig(AppConfig())
    star = None
    for s in rig.engine.catalog.stars:
        if s["mag"] > 2.0:
            break
        alt, az = (float(x) for x in rig.engine.altaz(rig.engine.resolve(f"hip:{s['hip']}"), T0 + 4 * 3600))
        if 4.0 < alt < 15.0 and 180.0 < az < 330.0:
            star = f"hip:{s['hip']}"
            break
    assert star, "no setting star at this time"
    t = T0 + 4 * 3600
    plan = timelapse_plan(rig.ctl, star, t, 120.0, 30.0)
    grid = t + np.arange(0.0, 7201.0, 5.0)
    alt = rig.engine.altaz(rig.engine.resolve(star), grid)[0]
    crossing = float(grid[int(np.argmax(alt < 0.0))] - t)  # first sample below the horizon, from a 5 s grid
    assert plan["sets_in_s"] == pytest.approx(crossing, abs=30.0)
    assert plan["held_for_s"] == plan["sets_in_s"] < 7200
    assert plan["alt"]["end"] >= 0.0 or plan["alt"]["end"] < plan["alt"]["start"]


def test_timelapse_rejects_nonsense() -> None:
    rig = Rig(AppConfig())
    with pytest.raises(CommandError):
        timelapse_plan(rig.ctl, "hip:24608", T0, 0.5, 10.0)
    with pytest.raises(CommandError):
        timelapse_plan(rig.ctl, "hip:24608", T0, 10.0, 700.0)
    with pytest.raises(CommandError):
        timelapse_plan(rig.ctl, "no such object", T0, 10.0, 10.0)


# ---------------------------------------------------------------------------- HTTP
def test_endpoints_and_queue_add_many_over_the_socket(tmp_path) -> None:
    with TestClient(create_app(observatory=Observatory(state_dir=tmp_path))) as c:
        m = c.get("/api/mosaic", params={"id": "con:Cyg", "t": T0, "cols": 2, "rows": 2, "fov_w": 40, "fov_h": 30}).json()
        assert len(m["panels"]) == 4 and m["coverage"]["w"] > 40
        assert c.get("/api/mosaic", params={"id": "con:Cyg", "cols": 16, "rows": 16, "fov_w": 40, "fov_h": 30}).status_code == 400
        assert c.get("/api/mosaic", params={"id": "con:Cyg", "cols": 20, "fov_w": 40, "fov_h": 30}).status_code == 422
        tl = c.get("/api/timelapse", params={"id": "hip:24608", "t": T0, "minutes": 30, "interval_s": 5}).json()
        assert tl["frames"] == 360
        assert c.get("/api/timelapse", params={"id": "hip:24608", "minutes": 5, "interval_s": 600}).status_code == 400
        assert c.get("/api/timelapse", params={"id": "hip:24608", "minutes": 0}).status_code == 422
        with c.websocket_connect("/ws") as ws:
            import json

            ws.receive_text(), ws.receive_text()
            ws.send_text(json.dumps({"type": "request_control"}))
            while json.loads(ws.receive_text()).get("type") != "control_result":
                pass
            ws.send_text(json.dumps({"type": "command", "seq": 1, "cmd": "queue_add_many", "args": {"items": m["items"], "group": "mosaic"}}))
            ack = None
            while ack is None or ack.get("type") != "ack":
                ack = json.loads(ws.receive_text())
            assert ack["ok"] and len(ack["ids"]) == len(m["items"])
            ws.send_text(json.dumps({"type": "command", "seq": 2, "cmd": "queue_add_many", "args": {"items": [{"target": "nope"}]}}))
            ack = None
            while ack is None or ack.get("type") != "ack":
                ack = json.loads(ws.receive_text())
            assert not ack["ok"]
