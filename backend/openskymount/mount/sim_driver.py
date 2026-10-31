"""In-process simulated alt-az mount implementing :class:`MountDriver`."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..config import MountLimits, SimulationConfig
from .axis import AxisParams, AxisSim
from .driver import AxisStatus, Capabilities, DriverStatus, Fault, MountDriver
from .geometry import altaz_to_vec, tilt_matrix, vec_to_altaz

INJECTABLE_FAULTS = ("stall_alt", "stall_az", "overcurrent", "encoder_glitch", "disconnect", "homing_failure")
HOME_ALT = 0.0
HOME_AZ = 0.0
LIMIT_MARGIN = 2.0  # hard limit switches sit this far beyond the soft limits
SENSOR_DISAGREEMENT_DEG = 0.5
STEADY_STEP_S = 0.2


@dataclass
class _Cmd:
    at: float
    name: str
    args: tuple[Any, ...]


class SimulatedMountDriver(MountDriver):
    def __init__(self, limits: MountLimits, sim: SimulationConfig, start_time: float = 0.0) -> None:
        self.limits = limits
        self.sim = sim
        self.rng = np.random.default_rng(sim.seed)
        self._connected = False
        self._time = start_time
        self._queue: deque[_Cmd] = deque()
        self._estop = False
        self._faults: set[str] = set()
        self._injected: set[str] = set()
        self._homed = True  # the simulator starts at a known park position
        self._homing = False
        self.alt = AxisSim(self._params(sim.backlash_alt, limits.min_altitude, limits.max_altitude), HOME_ALT)
        self.az = AxisSim(self._params(sim.backlash_az, limits.az_min, limits.az_max), HOME_AZ)
        self._tilt = tilt_matrix(sim.tilt_magnitude, sim.tilt_direction)
        self._encoder_glitch = 0.0

    def _params(self, backlash: float, lo: float, hi: float) -> AxisParams:
        return AxisParams(
            max_rate=self.limits.max_rate,
            max_accel=self.limits.max_accel,
            backlash=backlash,
            hard_min=lo - LIMIT_MARGIN,
            hard_max=hi + LIMIT_MARGIN,
            soft_min=lo,
            soft_max=hi,
        )

    def reconfigure(self, limits: MountLimits, sim: SimulationConfig) -> None:
        """Apply new limits / imperfections without resetting the axis positions."""
        self.limits = limits
        self.sim = sim
        for axis, bl, lo, hi in (
            (self.alt, sim.backlash_alt, limits.min_altitude, limits.max_altitude),
            (self.az, sim.backlash_az, limits.az_min, limits.az_max),
        ):
            axis.p = self._params(bl, lo, hi)
        self._tilt = tilt_matrix(sim.tilt_magnitude, sim.tilt_direction)

    # ----------------------------------------------------------- interface
    def capabilities(self) -> Capabilities:
        lim = self.limits
        return Capabilities(
            name="OpenSkyMount simulator",
            kind="simulated",
            simulated=True,
            supports_time_warp=True,
            has_axis_encoders=self.sim.has_axis_encoders,
            has_homing_sensors=True,
            max_rate=lim.max_rate,
            max_accel=lim.max_accel,
            alt_min=lim.min_altitude,
            alt_max=lim.max_altitude,
            az_min=lim.az_min,
            az_max=lim.az_max,
            firmware="sim-1",
        )

    def connect(self) -> None:
        if "disconnect" in self._injected:
            raise ConnectionError("simulated link is down")
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False
        self.alt.command_stop()
        self.az.command_stop()

    def _enqueue(self, name: str, *args: Any) -> None:
        if not self._connected or "disconnect" in self._injected:
            return  # commands are lost on a dead link
        self._queue.append(_Cmd(self._time + self.sim.command_latency_s, name, args))

    def move_to(self, alt: float, az: float, alt_rate: float = 0.0, az_rate: float = 0.0) -> None:
        self._enqueue("move_to", alt, az, alt_rate, az_rate)

    def set_rates(self, alt_rate: float, az_rate: float) -> None:
        self._enqueue("set_rates", alt_rate, az_rate)

    def stop(self) -> None:
        self._enqueue("stop")

    def emergency_stop(self) -> None:
        # Modelled as a dedicated, immediate line: no latency, cannot be queued behind motion.
        self._queue.clear()
        self._estop = True
        self.alt.hard_stop()
        self.az.hard_stop()

    def reset(self) -> None:
        if self.sim.has_axis_encoders and Fault.SENSOR_DISAGREEMENT in self._faults:
            # Lost steps: trust the absolute axis encoders and re-reference the step counters.
            # Idealisation: the firmware also knows on which side of the gear slack it sits
            # (from the last motion direction), so the counter is set to the motor-side angle.
            for axis in (self.alt, self.az):
                axis.set_count(axis.motor)
                axis.hard_stop()
        self._estop = False
        self._faults.clear()
        self.alt.limit_hit = False
        self.az.limit_hit = False
        self._encoder_glitch = 0.0
        self.alt.stalled = "stall_alt" in self._injected
        self.az.stalled = "stall_az" in self._injected

    def home(self) -> None:
        self._enqueue("home")

    def sync_position(self, alt: float, az: float) -> None:
        self._enqueue("sync", alt, az)

    def inject_fault(self, fault: str, active: bool = True) -> None:
        if fault not in INJECTABLE_FAULTS:
            raise ValueError(f"unknown fault {fault!r}; choose from {INJECTABLE_FAULTS}")
        if active:
            self._injected.add(fault)
        else:
            self._injected.discard(fault)
        self.alt.stalled = "stall_alt" in self._injected
        self.az.stalled = "stall_az" in self._injected
        if fault == "encoder_glitch":
            self._encoder_glitch = 3.0 if active else 0.0
        if fault == "overcurrent" and active:
            self._faults.add(Fault.OVERCURRENT)
            self.alt.hard_stop()
            self.az.hard_stop()
        if fault == "disconnect" and active:
            self._queue.clear()
            self.alt.command_stop()  # firmware safe-state on link loss: controlled stop
            self.az.command_stop()

    @property
    def injected_faults(self) -> list[str]:
        return sorted(self._injected)

    # ------------------------------------------------------------- physics
    def _apply(self, cmd: _Cmd) -> None:
        if self._estop or Fault.OVERCURRENT in self._faults:
            return
        if cmd.name == "move_to":
            alt, az, ar, zr = cmd.args
            self.alt.command_position(alt, ar)
            self.az.command_position(az, zr)
        elif cmd.name == "set_rates":
            self.alt.command_velocity(cmd.args[0])
            self.az.command_velocity(cmd.args[1])
        elif cmd.name == "stop":
            self.alt.command_stop()
            self.az.command_stop()
        elif cmd.name == "home":
            self._homing = True
            self._homed = False
            self.alt.command_position(HOME_ALT)
            self.az.command_position(HOME_AZ)
        elif cmd.name == "sync":
            self.alt.set_count(cmd.args[0])
            self.az.set_count(cmd.args[1])
            self.alt.hard_stop()
            self.az.hard_stop()

    def _finish_homing(self) -> None:
        if "homing_failure" in self._injected:
            self._faults.add(Fault.HOMING_FAILED)
            self._homing = False
            return
        # Home switches reference the *true* output position: re-zero the step counters
        # so the reported position equals the physical switch position.
        # (Idealised: approach direction known, so the counter matches the motor-side angle.)
        self.alt.set_count(self.alt.motor)
        self.az.set_count(self.az.motor)
        self.alt.command_position(HOME_ALT)
        self.az.command_position(HOME_AZ)
        self._homing = False
        self._homed = True

    def update(self, sim_time: float) -> None:
        if sim_time < self._time:
            # Time moved backwards (clock reset): physics cannot run backwards; re-anchor.
            self._time = sim_time
            for c in self._queue:
                c.at = min(c.at, sim_time)
            return
        while self._time < sim_time - 1e-12:
            # Fine steps while accelerating; coarse steps in steady state (sidereal tracking),
            # where the motion is smooth and the result is the same within micro-degrees.
            dt_max = self.sim.physics_step_s if (self._homing or self.alt.active or self.az.active) else STEADY_STEP_S
            dt = min(dt_max, sim_time - self._time)
            if self._queue:
                dt = min(dt, max(self._queue[0].at - self._time, self.sim.physics_step_s))
            while self._queue and self._queue[0].at <= self._time:
                self._apply(self._queue.popleft())
            if not self._estop:
                self.alt.step(dt)
                self.az.step(dt)
            self._time += dt
            if self._homing and not self.alt.moving and not self.az.moving and \
                    abs(self.alt.count - self.alt.target) < 1e-3 and abs(self.az.count - self.az.target) < 1e-3:
                self._finish_homing()
        while self._queue and self._queue[0].at <= self._time:
            self._apply(self._queue.popleft())
        self._check_faults()

    def _encoder_reading(self, axis: AxisSim) -> float:
        # Encoders are referenced to the same home switches as the step counters, so the
        # static zero-offset errors (true north / level) are invisible to them.
        noise = float(self.rng.normal(0.0, self.sim.encoder_noise)) if self.sim.encoder_noise > 0 else 0.0
        return axis.output + noise + self._encoder_glitch

    def _check_faults(self) -> None:
        if self.alt.limit_hit:
            self._faults.add(Fault.LIMIT_ALT)
        if self.az.limit_hit:
            self._faults.add(Fault.LIMIT_AZ)
        if self.sim.has_axis_encoders:
            for axis, code in ((self.alt, Fault.STALL_ALT), (self.az, Fault.STALL_AZ)):
                enc = axis.output + self._encoder_glitch
                slack = axis.p.backlash / 2.0 + 0.05
                if abs(axis.count - enc) > SENSOR_DISAGREEMENT_DEG + slack:
                    self._faults.add(Fault.SENSOR_DISAGREEMENT)
                    if axis.stalled:
                        self._faults.add(code)
        if self._faults and not self._estop:
            # Firmware safe state on any fault: stop both axes.
            self.alt.command_stop()
            self.az.command_stop()

    def true_pointing(self) -> tuple[float, float]:
        """Physical sky direction of the payload (ground truth)."""
        v = altaz_to_vec(self.alt.output + self.sim.alt_zero_offset, self.az.output + self.sim.az_zero_offset)
        return vec_to_altaz(self._tilt @ v)

    def status(self) -> DriverStatus:
        link_ok = self._connected and "disconnect" not in self._injected
        faults = sorted(self._faults | ({Fault.CONNECTION_LOST} if self._connected and not link_ok else set()))
        if self._estop:
            faults = sorted(set(faults) | {Fault.ESTOP})
        enc = self.sim.has_axis_encoders
        t_alt, t_az = self.true_pointing()
        return DriverStatus(
            connected=link_ok,
            alt=AxisStatus(
                position=self.alt.count, velocity=self.alt.vel,
                encoder=self._encoder_reading(self.alt) if enc else None,
                moving=self.alt.moving, at_limit=self.alt.limit_hit,
            ),
            az=AxisStatus(
                position=self.az.count, velocity=self.az.vel,
                encoder=self._encoder_reading(self.az) if enc else None,
                moving=self.az.moving, at_limit=self.az.limit_hit,
            ),
            homed=self._homed,
            homing=self._homing,
            estop=self._estop,
            faults=faults,
            timestamp=self._time,
            truth={
                "alt": t_alt,
                "az": t_az,
                "alt_axis": self.alt.output,
                "az_axis": self.az.output,
                "alt_motor": self.alt.motor,
                "az_motor": self.az.motor,
            },
            motor={
                "alt_mode": self.alt.mode,
                "az_mode": self.az.mode,
                "queued_commands": len(self._queue),
                "injected_faults": self.injected_faults,
            },
        )
