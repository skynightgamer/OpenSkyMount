"""Slew preview: read-only, consistent with what the simulated mount then does."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from openskymount.config import AppConfig
from openskymount.mount.controller import CommandError
from openskymount.mount.geometry import separation
from openskymount.mount.preview import axis_time, axis_travel, slew_preview
from openskymount.server.app import Observatory, create_app

from .conftest import T0, Rig


def park_at_azimuth_axis(rig: Rig, az_axis: float) -> None:
    """Move the azimuth axis to a multi-turn value in steps of at most 120 degrees (a GoTo takes the nearest turn)."""
    here = rig.ctl.status_cache.az.position
    while abs(az_axis - here) > 0.1:
        here += max(-120.0, min(120.0, az_axis - here))
        rig.ctl.goto(f"altaz:20,{here % 360.0}", track=False)
        rig.run(60.0)
        assert rig.ctl.mode == "idle"
    assert rig.ctl.status_cache.az.position == pytest.approx(az_axis, abs=0.2)


def test_axis_time_matches_the_textbook_trapezoid() -> None:
    # Reaches 6 deg/s after 1 s (accel 6): 180 deg takes 30 s cruising + 1 s of acceleration/braking overlap.
    assert axis_time(180.0, 6.0, 6.0) == pytest.approx(31.0)
    # A short move never reaches full speed: d = a t^2 / 4  ->  t = 2 sqrt(d / a).
    assert axis_time(1.5, 6.0, 6.0) == pytest.approx(1.0)
    assert axis_time(0.0, 6.0, 6.0) == 0.0
    assert axis_time(-90.0, 6.0, 6.0) == axis_time(90.0, 6.0, 6.0)


@pytest.mark.parametrize("d", [1.5, 40.0, -250.0])
def test_axis_travel_goes_from_zero_to_the_distance_without_overshoot(d: float) -> None:
    total = axis_time(d, 6.0, 6.0)
    assert axis_travel(d, 6.0, 6.0, 0.0) == 0.0
    assert axis_travel(d, 6.0, 6.0, total) == pytest.approx(d)
    prev = 0.0
    for k in range(1, 41):
        x = axis_travel(d, 6.0, 6.0, total * k / 40)
        assert abs(x) >= abs(prev) - 1e-9 and abs(x) <= abs(d) + 1e-9
        prev = x


def test_preview_does_not_touch_the_controller() -> None:
    rig = Rig(AppConfig())
    ctl = rig.ctl
    before = (ctl.command_serial, ctl.mode, ctl.target, ctl.track, ctl._az_cmd, len(ctl.events))
    plan = slew_preview(ctl, "planet:jupiter")
    assert plan["refused"] is None
    assert (ctl.command_serial, ctl.mode, ctl.target, ctl.track, ctl._az_cmd, len(ctl.events)) == before


@pytest.mark.parametrize("target", ["planet:jupiter", "hip:32349", "con:Ori"])
def test_estimated_duration_matches_the_simulated_slew(target: str) -> None:
    rig = Rig(AppConfig())
    plan = slew_preview(rig.ctl, target)
    est = plan["duration_s"]
    assert 5.0 < est < 120.0
    rig.ctl.goto(target, track=False)
    start = rig.t
    while rig.ctl.mode == "slewing" and rig.t - start < 300:
        rig.run(0.5)
    assert rig.ctl.mode == "idle"
    actual = rig.t - start
    assert actual == pytest.approx(est, rel=0.2, abs=3.0), (est, actual)


def test_path_runs_from_here_to_the_target() -> None:
    rig = Rig(AppConfig())
    plan = slew_preview(rig.ctl, "planet:jupiter")
    path = plan["path"]
    assert len(path) >= 20
    assert separation(path[0][0], path[0][1], *rig.ctl.pointing()) < 0.01
    assert separation(path[-1][0], path[-1][1], plan["to"]["alt"], plan["to"]["az"]) < 0.01
    assert plan["separation_deg"] == pytest.approx(separation(*rig.ctl.pointing(), plan["to"]["alt"], plan["to"]["az"]))
    assert plan["turn"]["alt"] == "up" and "clockwise" in plan["turn"]["az"]


def test_refused_targets_say_why_and_have_no_path() -> None:
    rig = Rig(AppConfig())
    outside = slew_preview(rig.ctl, "altaz:-10.0,100.0")  # below the lower mechanical limit
    assert outside["refused"] and "mechanical" in outside["refused"] and "path" not in outside
    never_rises = slew_preview(rig.ctl, "hip:60718")  # Acrux, never above the horizon from Berlin
    assert never_rises["refused"] and "altitude limit" in never_rises["refused"]
    sun = slew_preview(rig.ctl, "sun")
    assert sun["refused"] and "solar" in sun["refused"].lower()
    rig.ctl.emergency_stop()
    assert "emergency stop" in slew_preview(rig.ctl, "planet:jupiter")["refused"]
    with pytest.raises(CommandError):
        slew_preview(rig.ctl, "nonsense target")


def test_long_way_round_is_reported_when_the_short_way_crosses_the_cable_limit() -> None:
    rig = Rig(AppConfig())
    park_at_azimuth_axis(rig, 260.0)
    plan = slew_preview(rig.ctl, "altaz:20,280")  # 20 degrees on, but beyond the +270 limit
    cable = plan["cable"]
    assert cable["long_way"] and plan["delta"]["az"] == pytest.approx(-340.0, abs=0.5)
    assert any("long way" in w for w in plan["warnings"])
    assert cable["now"] == pytest.approx(260.0, abs=0.2) and cable["after"] == pytest.approx(-80.0, abs=0.5)
    # ...and the simulated mount takes that route.
    rig.ctl.goto("altaz:20,280", track=False)
    rig.run(120.0)
    assert rig.ctl.status_cache.az.position == pytest.approx(-80.0, abs=0.5)
    assert plan["duration_s"] > 40.0


def test_near_limit_warning() -> None:
    rig = Rig(AppConfig())
    park_at_azimuth_axis(rig, 262.0)
    plan = slew_preview(rig.ctl, "altaz:20,265")
    assert plan["cable"]["margin_after"] == pytest.approx(5.0, abs=0.5)
    assert any("ends 5° from a cable-wrap limit" in w.lower().replace("the goto ", "") or "5° from a cable-wrap limit" in w for w in plan["warnings"])


def _star_in_the_south_west(rig: Rig) -> str:
    """A bright star that is up in the south-west at T0 and drifts further west (azimuth increasing)."""
    for star in rig.engine.catalog.stars:
        if star["mag"] > 2.5:
            break
        target = rig.engine.resolve(f"hip:{star['hip']}")
        alt, az = (float(x) for x in rig.engine.altaz(target, T0))
        if alt > 12.0 and 215.0 < az < 255.0:
            return target.id
    raise AssertionError("no suitable star")


@pytest.mark.parametrize("unwind", [True, False])
def test_tracking_limit_time_is_when_the_target_reaches_the_cable_limit(unwind: bool) -> None:
    cfg = AppConfig()
    cfg.control.auto_unwind = unwind
    rig = Rig(cfg)
    star = _star_in_the_south_west(rig)
    park_at_azimuth_axis(rig, 200.0)  # the GoTo then keeps the azimuth on this turn instead of wrapping to the other
    # Park far enough round that the star's azimuth is reached near the +270 limit after a while.
    plan = slew_preview(rig.ctl, star)
    limit_s = plan["cable"]["tracking_limit_s"]
    assert limit_s is not None and limit_s > 0
    arrival = plan["duration_s"]
    az_after = plan["cable"]["after"]
    # Independent check: follow the star with the astronomy engine; the azimuth, on the same turn, reaches +270.
    target = rig.engine.resolve(star)
    az_now = float(rig.engine.altaz(target, T0 + arrival)[1])
    az_later = float(rig.engine.altaz(target, T0 + arrival + limit_s)[1])
    assert az_after + ((az_later - az_now + 540.0) % 360.0 - 180.0) == pytest.approx(270.0, abs=0.3)
    warned = [w for w in plan["warnings"] if "Tracking would reach the cable-wrap limit" in w]
    assert limit_s < 3 * 3600 and len(warned) == 1  # this star gets there in about two hours
    assert ("will unwind 360°" in warned[0]) == unwind
    assert ("Tracking will stop there" in warned[0]) == (not unwind)


def test_endpoint(tmp_path) -> None:
    with TestClient(create_app(observatory=Observatory(state_dir=tmp_path))) as c:
        d = c.get("/api/slew_preview", params={"id": "planet:jupiter"}).json()
        assert d["target"]["id"] == "planet:jupiter"
        if d["refused"] is None:
            assert d["duration_s"] > 0 and len(d["path"]) > 5
        r = c.get("/api/slew_preview", params={"id": "nonsense target"})
        assert r.status_code == 404
        assert "refused" in c.get("/api/slew_preview", params={"id": "sun"}).json()
    assert T0 > 0
