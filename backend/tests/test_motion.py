"""Axis physics and simulated driver tests."""

from __future__ import annotations

from openskymount.config import MountLimits, SimulationConfig
from openskymount.mount.axis import AxisParams, AxisSim
from openskymount.mount.driver import Fault
from openskymount.mount.sim_driver import SimulatedMountDriver


def run_axis(axis: AxisSim, seconds: float, dt: float = 0.01) -> list[tuple[float, float]]:
    trace = []
    for _ in range(round(seconds / dt)):
        axis.step(dt)
        trace.append((axis.count, axis.vel))
    return trace


def test_slew_converges_and_respects_limits() -> None:
    p = AxisParams(max_rate=5.0, max_accel=4.0)
    ax = AxisSim(p)
    ax.command_position(90.0)
    trace = run_axis(ax, 30.0)
    assert abs(ax.count - 90.0) < 1e-3
    vels = [v for _, v in trace]
    assert max(abs(v) for v in vels) <= 5.0 + 1e-9
    accels = [(b - a) / 0.01 for a, b in zip(vels, vels[1:], strict=False)]
    assert max(abs(a) for a in accels) <= 4.0 + 1e-6
    # No significant overshoot.
    assert max(c for c, _ in trace) < 90.0 + 0.01


def test_slew_time_is_physically_plausible() -> None:
    # Trapezoid: 90 deg at 5 deg/s, 4 deg/s^2 -> 90/5 + 5/4 = 19.25 s (+ final approach)
    ax = AxisSim(AxisParams(max_rate=5.0, max_accel=4.0))
    ax.command_position(90.0)
    t = 0.0
    while abs(ax.count - 90.0) > 0.01:
        ax.step(0.01)
        t += 0.01
    assert 19.0 < t < 22.0


def test_tracking_with_feedforward_follows_ramp() -> None:
    ax = AxisSim(AxisParams(max_rate=5.0, max_accel=4.0))
    rate = 0.004  # deg/s, sidereal-ish
    target = 0.0
    for i in range(20000):
        target = rate * i * 0.01
        ax.command_position(target, rate)
        ax.step(0.01)
    assert abs(ax.count - target) < 1e-4


def test_stop_decelerates_within_bounds() -> None:
    ax = AxisSim(AxisParams(max_rate=6.0, max_accel=3.0))
    ax.command_velocity(6.0)
    run_axis(ax, 5.0)
    assert abs(ax.vel - 6.0) < 1e-9
    start = ax.count
    ax.command_stop()
    run_axis(ax, 5.0)
    assert ax.vel == 0.0 and ax.mode == AxisSim.IDLE
    assert abs((ax.count - start) - 6.0**2 / (2 * 3.0)) < 0.05


def test_backlash_deadband() -> None:
    ax = AxisSim(AxisParams(backlash=0.2))
    ax.command_position(5.0)
    run_axis(ax, 10.0)
    assert abs(ax.output - (5.0 - 0.1)) < 1e-3  # output lags by half the backlash
    ax.command_position(4.0)
    run_axis(ax, 10.0)
    assert abs(ax.output - (4.0 + 0.1)) < 1e-3  # after reversal it lags on the other side


def test_stall_counts_steps_without_motion() -> None:
    ax = AxisSim(AxisParams())
    ax.stalled = True
    ax.command_position(10.0)
    run_axis(ax, 10.0)
    assert abs(ax.count - 10.0) < 1e-3 and ax.output == 0.0


def test_hard_limit_stops_axis() -> None:
    ax = AxisSim(AxisParams(hard_min=-10.0, hard_max=10.0))
    ax.command_velocity(5.0)
    run_axis(ax, 5.0)
    assert ax.limit_hit and ax.vel == 0.0 and ax.output <= 10.0


def make_driver(**sim_kw) -> SimulatedMountDriver:
    d = SimulatedMountDriver(MountLimits(), SimulationConfig(**sim_kw), start_time=0.0)
    d.connect()
    return d


def test_driver_latency_and_determinism() -> None:
    traces = []
    for _ in range(2):
        d = make_driver(command_latency_s=0.2)
        d.move_to(30.0, 45.0)
        d.update(0.1)
        assert d.status().alt.position == 0.0  # command not yet delivered
        out = []
        for i in range(1, 200):
            d.update(0.1 + i * 0.05)
            s = d.status()
            out.append((s.alt.position, s.az.position, s.alt.encoder))
        traces.append(out)
    assert traces[0] == traces[1]
    assert abs(traces[0][-1][0] - 30.0) < 0.01


def test_driver_estop_is_immediate_and_latched() -> None:
    d = make_driver()
    d.set_rates(5.0, 5.0)
    d.update(3.0)
    assert d.status().alt.velocity > 1.0
    d.emergency_stop()
    s = d.status()
    assert s.estop and s.alt.velocity == 0.0 and Fault.ESTOP in s.faults
    d.move_to(10.0, 10.0)
    d.update(10.0)
    assert d.status().alt.velocity == 0.0  # ignored while latched
    d.reset()
    d.move_to(10.0, 10.0)
    d.update(30.0)
    assert abs(d.status().alt.position - 10.0) < 0.01


def test_driver_stall_detected_by_encoders() -> None:
    d = make_driver()
    d.inject_fault("stall_az")
    d.move_to(0.0, 30.0)
    d.update(20.0)
    s = d.status()
    assert Fault.SENSOR_DISAGREEMENT in s.faults and Fault.STALL_AZ in s.faults


def test_driver_disconnect_drops_commands_and_stops() -> None:
    d = make_driver()
    d.set_rates(0.0, 4.0)
    d.update(2.0)
    d.inject_fault("disconnect")
    assert not d.status().connected
    d.update(10.0)
    assert d.status().az.velocity == 0.0  # firmware safe state: controlled stop
    d.set_rates(0.0, 4.0)  # lost on the dead link
    d.update(12.0)
    assert d.status().az.velocity == 0.0
    d.inject_fault("disconnect", False)
    d.connect()
    assert d.status().connected


def test_homing_rereferences_after_missed_steps() -> None:
    d = make_driver(encoder_noise=0.0, backlash_alt=0.0, backlash_az=0.0)
    d.inject_fault("stall_az")
    d.move_to(0.0, 0.4)  # small enough to stay below the disagreement threshold
    d.update(5.0)
    d.inject_fault("stall_az", False)
    s = d.status()
    assert abs(s.az.position - 0.4) < 1e-3 and abs(s.truth["az_axis"]) < 1e-9
    d.home()
    d.update(20.0)
    s = d.status()
    assert s.homed and abs(s.az.position - s.truth["az_axis"]) < 1e-3


def test_homing_failure_reported() -> None:
    d = make_driver()
    d.inject_fault("homing_failure")
    d.move_to(10.0, 10.0)
    d.update(10.0)
    d.home()
    d.update(30.0)
    assert Fault.HOMING_FAILED in d.status().faults
