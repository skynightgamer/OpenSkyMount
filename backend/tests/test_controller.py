"""Controller tests: GoTo, tracking, limits, safety, faults, time handling."""

from __future__ import annotations

import pytest

from openskymount.config import AppConfig, MountLimits
from openskymount.mount.controller import CommandError, Mode

from .conftest import T0, Rig


def start_when_between(rig_factory, target: str, lo: float, hi: float, cfg: AppConfig | None = None) -> Rig:
    """Create a rig at the first hour (from T0) when the target's altitude is in [lo, hi]."""
    cfg = cfg or AppConfig()
    probe = Rig(cfg)
    tgt = probe.engine.resolve(target)
    for h in range(0, 48):
        t = T0 + h * 3600
        alt, _ = probe.engine.altaz(tgt, t)
        if lo <= float(alt) <= hi and float(probe.engine.sun_altaz(t)[0]) < -12:
            return rig_factory(cfg, t)
    pytest.skip(f"{target} not observable in window")


def test_goto_jupiter_converges(rig: Rig) -> None:
    rig.ctl.goto("jupiter")
    snap = rig.run(60)
    assert snap["mode"] == Mode.TRACKING
    tol = rig.cfg.control.goto_tolerance_deg
    assert snap["true_pointing_error"] < tol
    assert snap["tracking_error"] < tol
    assert snap["target"]["name"] == "Jupiter"


@pytest.mark.parametrize("target", ["Vega", "Capella", "jupiter", "moon", "M31", "Polaris", "Deneb"])
def test_tracking_stays_within_tolerance(make_rig, target: str) -> None:
    rig = start_when_between(make_rig, target, 15, 75)
    rig.ctl.goto(target)
    snap = rig.run(90, dt=0.25)
    assert snap["mode"] == Mode.TRACKING, snap["events"]
    tol = rig.cfg.control.tracking_tolerance_deg
    worst = 0.0
    # Two hours of accelerated simulated time, sampled every simulated minute.
    for _ in range(120):
        snap = rig.run(60, dt=5.0)
        if snap["mode"] != Mode.TRACKING:
            break  # e.g. target set below the limit; checked below
        worst = max(worst, snap["true_pointing_error"])
    assert worst < tol, f"{target}: worst true error {worst * 3600:.1f} arcsec"
    if snap["mode"] != Mode.TRACKING:
        assert snap["target"]["alt"] < rig.cfg.control.min_target_altitude + 0.5


def test_polaris_azimuth_wrap_does_not_spin(make_rig) -> None:
    """Polaris' azimuth oscillates around 0/360: the mount must not take the long way round."""
    rig = make_rig(AppConfig())
    rig.ctl.goto("Polaris")
    rig.run(60)
    for _ in range(12 * 6):  # 12 h: Polaris' azimuth passes through 0/360 twice
        snap = rig.run(600, dt=20.0)
        assert snap["mode"] == Mode.TRACKING
        assert -3.0 < snap["position"]["az_axis"] < 3.0
        assert snap["true_pointing_error"] < rig.cfg.control.tracking_tolerance_deg


def test_cable_wrap_unwind(make_rig) -> None:
    cfg = AppConfig(limits=MountLimits(az_min=-180.0, az_max=180.0))
    # A southern star transiting the meridian (az 180) forces an unwind with these limits.
    rig = make_rig(cfg)
    tgt = "radec:0,10"
    target = rig.engine.resolve(tgt)
    # Start ~40 min before meridian transit.
    for minute in range(0, 24 * 60, 5):
        t = T0 + minute * 60
        _, az = rig.engine.altaz(target, t)
        if 168 < float(az) < 172:
            rig = make_rig(cfg, t)
            break
    rig.ctl.goto(tgt)
    snap = rig.run(120)
    assert snap["mode"] == Mode.TRACKING
    assert snap["position"]["az_axis"] > 0
    msgs = []
    for _ in range(120):
        snap = rig.run(60, dt=2.0)
        msgs += [e["msg"] for e in snap["events"]]
    assert any("unwinding" in m for m in msgs)
    assert snap["mode"] == Mode.TRACKING
    assert snap["position"]["az_axis"] < 0  # now on the other side of the wrap
    assert -180 <= snap["position"]["az_axis"] <= 180


def test_refuses_sun_and_sun_proximity(rig: Rig) -> None:
    with pytest.raises(CommandError, match="Sun"):
        rig.ctl.goto("sun")
    # A position 5 degrees from the Sun.
    rd = rig.engine.radec(rig.engine.resolve("sun"), rig.t)
    with pytest.raises(CommandError):
        rig.ctl.goto(f"radec:{rd['ra_j2000']:.3f},{max(-89, rd['dec_j2000'] - 5):.3f}")


def test_solar_targets_only_with_explicit_sim_config(make_rig) -> None:
    cfg = AppConfig()
    cfg.control.allow_solar_targets = True
    # Daytime start so the Sun is up.
    for h in range(0, 24):
        probe = make_rig(cfg, T0 + h * 3600)
        if float(probe.engine.sun_altaz(probe.t)[0]) > 15:
            probe.ctl.goto("sun")
            assert probe.run(60)["mode"] == Mode.TRACKING
            return
    pytest.skip("Sun never high")


def test_refuses_below_horizon_and_invalid(rig: Rig) -> None:
    below = None
    for s in rig.engine.catalog.stars[:100]:
        alt, _ = rig.engine.altaz(rig.engine.resolve(f"hip:{s['hip']}"), rig.t)
        if float(alt) < -10:
            below = f"hip:{s['hip']}"
            break
    assert below
    with pytest.raises(CommandError, match="below"):
        rig.ctl.goto(below)
    with pytest.raises(CommandError):
        rig.ctl.goto("not-a-real-object-qq")
    with pytest.raises(CommandError):
        rig.ctl.goto("altaz:-30,10")
    assert rig.ctl.mode == Mode.IDLE


def test_payload_altitude_limit(make_rig) -> None:
    cfg = AppConfig(payload="telescope")
    rig = make_rig(cfg)
    assert rig.ctl.limits.max_altitude == 82.0
    with pytest.raises(CommandError, match="reachable|limits"):
        rig.ctl.goto("altaz:85,120")
    rig.ctl.goto("altaz:80,120")
    snap = rig.run(120)
    assert snap["mode"] == Mode.IDLE
    assert abs(snap["position"]["alt"] - 80) < 0.05


def test_time_jump_reacquires(rig: Rig) -> None:
    rig.ctl.goto("Capella")
    rig.run(60)
    snap = rig.jump(rig.t + 1800)
    assert snap["mode"] == Mode.SLEWING
    snap = rig.run(60)
    assert snap["mode"] == Mode.TRACKING
    assert snap["true_pointing_error"] < rig.cfg.control.tracking_tolerance_deg


def test_time_jump_to_target_below_horizon_stops(rig: Rig) -> None:
    rig.ctl.goto("jupiter")
    rig.run(60)
    snap = rig.jump(rig.t + 14 * 3600)  # daytime, Jupiter set
    assert snap["mode"] == Mode.IDLE
    assert any("below the horizon" in e["msg"] for e in snap["events"])


def test_emergency_stop_precedence(rig: Rig) -> None:
    rig.ctl.goto("jupiter")
    rig.run(3)
    assert rig.ctl.status_cache.az.velocity != 0
    rig.ctl.emergency_stop()
    snap = rig.run(1)
    assert snap["mode"] == Mode.STOPPED
    st = rig.driver.status()
    assert st.alt.velocity == 0 and st.az.velocity == 0
    with pytest.raises(CommandError, match="emergency"):
        rig.ctl.goto("jupiter")
    with pytest.raises(CommandError):
        rig.ctl.manual(1, 1, rig.wall)
    rig.ctl.reset()
    rig.ctl.goto("jupiter")
    assert rig.run(60)["mode"] == Mode.TRACKING


def test_manual_deadman(rig: Rig) -> None:
    rig.ctl.manual(0.0, 3.0, rig.wall)
    snap = rig.run(0.5)
    assert snap["mode"] == Mode.MANUAL
    snap = rig.run(2.0)  # no refresh for > 1 s wall time
    assert snap["mode"] == Mode.IDLE
    rig.run(3.0)
    assert not rig.driver.status().az.moving


def test_manual_deadman_uses_wall_time_not_sim_time(rig: Rig) -> None:
    # At 100x simulation speed, 2 s of sim time is 20 ms of wall time: still alive.
    rig.ctl.manual(0.0, 3.0, rig.wall)
    snap = rig.run(2.0, dt=0.25, wall_dt=0.0025)
    assert snap["mode"] == Mode.MANUAL


def test_manual_soft_limit(rig: Rig) -> None:
    for _ in range(80):  # keep refreshing like a held button
        rig.ctl.manual(5.0, 0.0, rig.wall)
        rig.run(0.25)
    st = rig.driver.status()
    assert st.alt.position <= rig.ctl.limits.max_altitude + 0.01
    assert not st.faults
    assert rig.ctl.mode == Mode.IDLE


def test_fault_halts_and_reset(rig: Rig) -> None:
    rig.ctl.goto("jupiter")
    rig.driver.inject_fault("stall_az")
    snap = rig.run(30)
    assert snap["mode"] == Mode.FAULT
    assert "sensor_disagreement" in snap["driver"]["faults"]
    with pytest.raises(CommandError, match="fault"):
        rig.ctl.goto("jupiter")
    rig.driver.inject_fault("stall_az", False)
    rig.ctl.reset()  # encoders re-reference the step counters
    rig.ctl.home()
    snap = rig.run(120)
    assert snap["mode"] == Mode.IDLE and snap["driver"]["homed"]
    rig.ctl.goto("jupiter")
    assert rig.run(90)["mode"] == Mode.TRACKING


def test_disconnect_and_reconnect(rig: Rig) -> None:
    rig.ctl.goto("jupiter")
    rig.run(3)
    rig.driver.inject_fault("disconnect")
    snap = rig.run(10)
    assert snap["mode"] == Mode.DISCONNECTED
    assert not rig.driver.alt.moving and not rig.driver.az.moving  # firmware safe stop
    rig.driver.inject_fault("disconnect", False)
    rig.ctl.reset()
    snap = rig.run(5)
    assert snap["mode"] == Mode.IDLE  # no automatic resumption of the old slew
    assert not rig.driver.az.moving


def test_pointing_correction_from_measurement(make_rig) -> None:
    cfg = AppConfig()
    cfg.sim.az_zero_offset = 0.5
    cfg.sim.alt_zero_offset = -0.3
    rig = make_rig(cfg)
    rig.ctl.goto("Capella")
    snap = rig.run(60)
    assert snap["true_pointing_error"] > 0.3  # uncalibrated mount misses
    truth = snap["driver"]["truth"]
    tgt = snap["target"]
    # A plate solver would measure the true pointing; feed back requested - measured.
    rig.ctl.apply_correction(tgt["alt"] - truth["alt"], ((tgt["az"] - truth["az"] + 180) % 360) - 180)
    snap = rig.run(30)
    assert snap["true_pointing_error"] < 0.02


def test_homing(rig: Rig) -> None:
    rig.ctl.goto("altaz:30,100")
    rig.run(60)
    rig.ctl.home()
    snap = rig.run(60)
    assert snap["mode"] == Mode.IDLE
    assert abs(snap["position"]["alt_axis"]) < 0.01 and abs(snap["position"]["az_axis"]) < 0.01


def test_determinism(make_rig) -> None:
    snaps = []
    for _ in range(2):
        rig = make_rig(AppConfig())
        rig.ctl.goto("jupiter")
        rig.run(30)
        rig.ctl.manual(1.0, -2.0, rig.wall)
        rig.run(0.5)
        rig.ctl.goto("Capella")
        s = rig.run(60)
        s.pop("events")
        snaps.append(s)
    assert snaps[0] == snaps[1]


def test_zenith_pass_recovers(make_rig) -> None:
    cfg = AppConfig()
    lat = cfg.observer.latitude
    rig = make_rig(cfg)
    tgt = f"radec:120,{lat + 0.1:.3f}"
    target = rig.engine.resolve(tgt)
    for minute in range(0, 24 * 60, 2):
        t = T0 + minute * 60
        alt, _ = rig.engine.altaz(target, t)
        if 85.0 < float(alt) < 86.0 and float(rig.engine.altaz_rates(target, t)[2]) > 0:
            rig = make_rig(cfg, t)
            break
    rig.ctl.goto(tgt)
    rig.run(120)
    for _ in range(180):  # 90 min: through the transit and well past it
        snap = rig.run(30, dt=1.0)
    # It passed (almost) through the zenith and must have recovered afterwards.
    assert snap["mode"] == Mode.TRACKING
    assert snap["true_pointing_error"] < cfg.control.tracking_tolerance_deg
    assert snap["target"]["alt"] < 85.0


@pytest.mark.parametrize("dt", [0.05, 1.0, 10.0])
def test_steady_state_error_is_small_for_any_update_span(rig: Rig, dt: float) -> None:
    """Regression: setpoints must refer to the instant they are applied (no one-interval lead)."""
    rig.ctl.goto("jupiter")
    rig.run(60)
    snap = rig.run(600, dt=dt)
    assert snap["mode"] == Mode.TRACKING
    assert snap["true_pointing_error"] < 5.0 / 3600, snap["true_pointing_error"] * 3600
