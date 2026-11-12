"""LX200 helpers, the fake OnStep endpoint and OnStepMountDriver over TCP (real time, like test_protocol.py).

The fake is written from the LX200/OnStep command reference; passing here does not prove compatibility with real
OnStepX firmware (no hardware yet)."""

from __future__ import annotations

import socket
import time

import pytest

from openskymount.astro.engine import AstronomyEngine
from openskymount.config import AppConfig, MountLimits
from openskymount.mount.controller import CommandError, Mode, MountController
from openskymount.mount.fake_onstep import FakeOnStep, serve
from openskymount.mount.lx200 import LX200Error, format_alt, format_az, format_rate, has_seconds, parse_angle
from openskymount.mount.onstep_driver import OnStepMountDriver
from openskymount.server.app import make_driver

FAST = MountLimits(max_rate=20.0, max_accel=40.0, tracking_max_rate=20.0)
VERY_FAST = MountLimits(max_rate=60.0, max_accel=240.0, tracking_max_rate=60.0)  # long azimuth travel tests


def test_angle_format_and_parse() -> None:
    assert format_alt(30.5) == "+30*30'00"
    assert format_alt(-0.25) == "-00*15'00"
    assert format_alt(-0.0001) == "+00*00'00"
    assert format_az(359.99999) == "000*00'00"
    assert format_az(-90) == "270*00'00"
    assert parse_angle("+30*30'00") == 30.5
    assert parse_angle("-00*15") == -0.25
    assert parse_angle("123*45:36.5") == pytest.approx(123 + 45 / 60 + 36.5 / 3600)
    assert parse_angle("+12\xdf34:56") == pytest.approx(12 + 34 / 60 + 56 / 3600)
    assert has_seconds("+12*34'56") and not has_seconds("+12*34")
    for bad in ("", "12", "+12*61'00", "ab*cd", "+12*30'75"):
        with pytest.raises(LX200Error):
            parse_angle(bad)
    for v in (-89.99, -12.3456, 0.0, 45.0001, 89.9997):
        assert parse_angle(format_alt(v)) == pytest.approx(v, abs=0.5 / 3600)
    assert format_rate(0.0041781) == "0.004178" and format_rate(-2.5) == "2.5"


@pytest.fixture(params=[FAST])
def onstep(request):
    fake = FakeOnStep(MountLimits(**vars(request.param)))
    server = serve(fake, port=0)
    yield fake, server.server_address[1]
    server.shutdown()
    fake.shutdown()


def raw(port: int, data: bytes, n: int = 64) -> str:
    with socket.create_connection(("127.0.0.1", port)) as s:
        s.sendall(data)
        s.settimeout(1.0)
        time.sleep(0.05)
        try:
            return s.recv(n).decode()
        except TimeoutError:
            return ""


def test_fake_replies(onstep) -> None:
    _, port = onstep
    assert raw(port, b"\x06") == "A"
    assert raw(port, b":GVP#") == "On-Step#"
    assert raw(port, b":GA#") == "+00*00'00#"
    assert raw(port, b":Sa+95*00'00#") == "0"
    assert raw(port, b":Sa+30*00'00#:Sz045*00'00#:MA#") == "110"
    assert raw(port, b":Te#") == "0"  # the fake does no firmware tracking
    assert raw(port, b":XYZ#") == "0"


def run_until(ctl: MountController, cond, timeout: float) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        now = time.time()
        ctl.update(now, now)
        snap = ctl.snapshot(with_radec=False)
        if cond(snap):
            return snap
        time.sleep(0.02)
    raise AssertionError(f"condition not reached; last mode {ctl.mode}, events {list(ctl.events)[-3:]}, "
                         f"driver {ctl.driver.status().motor}")


def make_controller(port: int) -> MountController:
    cfg = AppConfig(limits=FAST)
    cfg.control.backlash_compensation = False  # backlash belongs to the simulated mount, not this firmware
    drv = make_driver(f"onstep://127.0.0.1:{port}", cfg, time.time())
    assert isinstance(drv, OnStepMountDriver)
    return MountController(AstronomyEngine(cfg.observer), drv, cfg, time.time())


def test_goto_manual_and_estop_through_controller(onstep) -> None:
    fake, port = onstep
    ctl = make_controller(port)
    caps = ctl.driver.capabilities()
    assert caps.kind == "lx200" and not caps.simulated and caps.firmware == "fake-0.1" and "On-Step" in caps.name
    run_until(ctl, lambda s: s["driver"]["connected"], 3)
    assert ":Td#" in fake.commands  # firmware tracking switched off at connect
    ctl.goto("altaz:30,40")
    snap = run_until(ctl, lambda s: s["mode"] == Mode.IDLE and s["tracking_error"] is not None
                     and s["tracking_error"] < 0.02, 20)
    assert abs(snap["position"]["alt"] - 30) < 0.02 and abs(snap["position"]["az"] - 40) < 0.02
    assert ":MA#" in fake.commands  # the long move was a firmware GoTo
    alt, az = fake.position()
    assert abs(alt - 30) < 0.02 and abs(az - 40) < 0.02
    assert snap["true_pointing_error"] is None  # hardware path: no ground truth
    # Manual slew: positive azimuth rate must turn the axis towards increasing azimuth.
    ctl.manual(0.0, 3.0, time.time())
    run_until(ctl, lambda s: s["position"]["az_rate"] > 2.0, 3)
    assert ":Mw#" in fake.commands
    ctl.emergency_stop()
    snap = run_until(ctl, lambda s: s["driver"]["estop"] and abs(s["position"]["az_rate"]) < 0.01, 3)
    assert snap["mode"] == Mode.STOPPED
    with pytest.raises(CommandError):
        ctl.goto("altaz:20,20")
    ctl.reset()
    run_until(ctl, lambda s: not s["driver"]["estop"] and s["mode"] == Mode.IDLE, 3)
    ctl.driver.disconnect()
    end = time.time() + 1  # the fake's server thread handles the last bytes a moment later
    while fake.commands[-1] != ":Q#" and time.time() < end:
        time.sleep(0.01)
    assert fake.commands[-1] == ":Q#"  # never leaves the mount moving


def test_fine_rate_loop_follows_a_moving_setpoint(onstep) -> None:
    """Tracking: once on target, the adapter follows a setpoint moving at 0.01/0.02°/s with rate commands only."""
    fake, port = onstep
    drv = OnStepMountDriver(f"lx200://127.0.0.1:{port}", FAST)
    drv.connect()
    end = time.monotonic() + 10
    while time.monotonic() < end:  # firmware GoTo to the start, then settle
        drv.update(0.0)
        drv.move_to(10.0, 20.0)
        st = drv.status()
        if abs(st.alt.position - 10) < 0.002 and abs(st.az.position - 20) < 0.002 and not st.az.moving:
            break
        time.sleep(0.05)
    n_goto = fake.commands.count(":MA#")
    t0 = time.monotonic()
    errors = []
    while time.monotonic() - t0 < 4.0:
        t = time.monotonic() - t0
        drv.update(t)
        drv.move_to(10.0 + 0.01 * t, 20.0 + 0.02 * t, 0.01, 0.02)
        st = drv.status()
        tr = t - st.motor["status_age_s"]  # the reading's own time
        if t > 1.5:
            errors.append(max(abs(st.alt.position - (10 + 0.01 * tr)), abs(st.az.position - (20 + 0.02 * tr))))
        time.sleep(0.05)
    drv.stop()
    assert max(errors) < 15 / 3600, f"worst error {max(errors) * 3600:.1f}″"
    assert fake.commands.count(":MA#") == n_goto  # no GoTo while tracking
    assert any(c.startswith(":RA") for c in fake.commands) and any(c.startswith(":RE") for c in fake.commands)
    drv.disconnect()


def settle(drv: OnStepMountDriver, alt: float, az: float, timeout: float = 25.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        drv.update(0.0)
        drv.move_to(alt, az)
        st = drv.status()
        if st.faults or (abs(st.az.position - az) < 0.01 and abs(st.alt.position - alt) < 0.01 and not st.az.moving):
            return
        time.sleep(0.05)


@pytest.mark.parametrize("onstep", [VERY_FAST], indirect=True)
def test_multi_turn_azimuth_and_sync_offset(onstep) -> None:
    fake, port = onstep
    drv = OnStepMountDriver(f"onstep://127.0.0.1:{port}", VERY_FAST)
    drv.connect()
    # Through the firmware's 0/360 seam and back: the adapter reports continuous (multi-turn) azimuth, and a 350°
    # move is split into legs so the firmware's shortest path is the way OpenSkyMount chose (cable wrap).
    for target in (-30.0, -200.0, 0.0):
        settle(drv, 20.0, target)
        st = drv.status()
        assert not st.faults and abs(st.az.position - target) < 0.01, (target, st.az.position, st.faults)
        assert abs(fake.position()[1] - target) < 0.01  # the firmware's own multi-turn axis agrees
    assert fake.commands.count(":MA#") >= 4  # the 200° move took two legs
    drv.sync_position(21.0, 1.0)
    st = drv.status()
    assert abs(st.alt.position - 21.0) < 1e-6 and abs(st.az.position - 1.0) < 1e-6
    drv.disconnect()


@pytest.mark.parametrize("onstep", [VERY_FAST], indirect=True)
def test_firmware_taking_the_long_way_round_faults(onstep) -> None:
    fake, port = onstep
    fake.limits.az_max = 120.0  # firmware travel narrower than OpenSkyMount's cable wrap (±270°)
    drv = OnStepMountDriver(f"onstep://127.0.0.1:{port}", VERY_FAST)
    drv.connect()
    settle(drv, 20.0, 150.0)  # firmware must go -210° instead of +150°
    st = drv.status()
    assert "goto_rejected" in st.faults and any("other way round" in e for e in st.motor["errors"])
    drv.disconnect()


def test_reversed_azimuth_wiring_is_detected(onstep) -> None:
    fake, port = onstep
    fake.reverse_az = True
    drv = OnStepMountDriver(f"onstep://127.0.0.1:{port}", FAST)
    drv.connect()
    end = time.monotonic() + 4
    while time.monotonic() < end and "direction_mismatch" not in drv.status().faults:
        drv.update(0.0)
        drv.set_rates(0.0, 2.0)
        time.sleep(0.05)
    assert "direction_mismatch" in drv.status().faults
    time.sleep(1.0)
    assert not fake.mount.status().az.moving  # stopped, not left running the wrong way
    drv.disconnect()


def test_host_watchdog_stops_motion(onstep) -> None:
    fake, port = onstep
    drv = OnStepMountDriver(f"onstep://127.0.0.1:{port}", FAST, host_watchdog_s=0.5)
    drv.connect()
    drv.update(0.0)
    drv.set_rates(0.0, 2.0)
    time.sleep(0.4)
    assert fake.mount.status().az.moving
    time.sleep(1.5)  # no update() calls: the controller loop is "hung"
    assert not fake.mount.status().az.moving
    assert any("controller silent" in e for e in drv.status().motor["errors"])
    drv.disconnect()


def test_link_loss_and_reconnect(onstep) -> None:
    fake, port = onstep
    ctl = make_controller(port)
    run_until(ctl, lambda s: s["driver"]["connected"], 3)
    fake.drop_connections()
    snap = run_until(ctl, lambda s: s["mode"] == Mode.DISCONNECTED, 4)
    assert "connection_lost" in snap["driver"]["faults"]
    ctl.reset()  # reconnects
    run_until(ctl, lambda s: s["mode"] == Mode.IDLE and s["driver"]["connected"], 4)
    ctl.driver.disconnect()


def test_handshake_checks_mode_and_precision() -> None:
    eq = FakeOnStep(FAST, mode="G")
    server = serve(eq, port=0)
    try:
        with pytest.raises(ConnectionError, match="equatorial"):
            OnStepMountDriver(f"onstep://127.0.0.1:{server.server_address[1]}", FAST).connect()
    finally:
        server.shutdown()
        eq.shutdown()
    low = FakeOnStep(FAST, high_precision=False)
    server = serve(low, port=0)
    try:
        drv = OnStepMountDriver(f"onstep://127.0.0.1:{server.server_address[1]}", FAST)
        drv.connect()
        assert ":U#" in low.commands and low.high_precision  # switched to arc-second replies
        drv.disconnect()
    finally:
        server.shutdown()
        low.shutdown()


def test_firmware_goto_refusal_becomes_a_fault(onstep) -> None:
    fake, port = onstep
    fake.limits.min_altitude = 5.0  # firmware limit stricter than OpenSkyMount's
    drv = OnStepMountDriver(f"onstep://127.0.0.1:{port}", FAST)
    drv.connect()
    end = time.monotonic() + 3
    while time.monotonic() < end and not drv.status().faults:
        drv.update(0.0)
        drv.move_to(2.0, 90.0)
        time.sleep(0.05)
    st = drv.status()
    assert "limit_alt" in st.faults and any("below the horizon" in e for e in st.motor["errors"])
    drv.reset()
    assert drv.status().faults == []
    drv.disconnect()


def test_solar_policy_is_strict_on_lx200_path(onstep) -> None:
    _, port = onstep
    ctl = make_controller(port)
    ctl.config.control.allow_solar_targets = True
    run_until(ctl, lambda s: s["driver"]["connected"], 3)
    with pytest.raises(CommandError, match="Sun"):
        ctl.goto("sun")
    ctl.driver.disconnect()
