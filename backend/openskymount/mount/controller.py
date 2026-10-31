"""High-level mount controller: GoTo, tracking, manual control, safety policy.

The controller is deterministic: it is advanced explicitly with
``update(sim_time, wall_time, clock_epoch)``. It converts astronomical targets
into axis setpoints for a :class:`MountDriver` at a fixed control rate (in
simulation time), so accelerated simulations behave exactly like real time,
only faster.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..astro.engine import AstronomyEngine, Target, TargetError
from ..config import AppConfig
from .driver import DriverStatus, MountDriver
from .geometry import field_rotation_rate, nearest_unwrapped, separation, wrap180

CONTROL_DT = 0.1  # s of simulation time between setpoints
STEADY_CONTROL_DT = 1.0  # while tracking smoothly with feed-forward
MAX_CONTROL_STEPS = 2500  # per update; larger spans use coarser steps
SETTLE_STEPS = 5
REACQUIRE_DEG = 1.0
SUN_CHECK_INTERVAL = 30.0


class Mode:
    IDLE = "idle"
    SLEWING = "slewing"
    TRACKING = "tracking"
    MANUAL = "manual"
    HOMING = "homing"
    STOPPED = "emergency_stop"
    FAULT = "fault"
    DISCONNECTED = "disconnected"


class CommandError(Exception):
    """A command was refused (invalid, unsafe or not possible in the current state)."""


class TargetTrack:
    """Target trajectory sampled with the ephemeris and linearly interpolated."""

    def __init__(self, engine: AstronomyEngine, target: Target) -> None:
        self.engine = engine
        self.target = target
        self.t = np.zeros(0)
        self.alt = np.zeros(0)
        self.az = np.zeros(0)  # continuous (np.unwrap'ed) azimuth

    def invalidate(self) -> None:
        self.t = np.zeros(0)

    def ensure(self, t0: float, t1: float) -> None:
        if len(self.t) and self.t[0] <= t0 and self.t[-1] >= t1:
            return
        # One vectorised ephemeris call covers the next few minutes (2 s sample spacing).
        span = max(120.0, (t1 - t0) * 4.0)
        n = int(min(600, span / 2.0)) + 1
        times = np.linspace(t0 - 1.0, t0 + span, n)
        alt, az = self.engine.altaz(self.target, times)
        self.t, self.alt = times, alt
        self.az = np.degrees(np.unwrap(np.radians(az)))

    def at(self, t: float) -> tuple[float, float, float, float]:
        self.ensure(t, t)
        i = int(np.clip(np.searchsorted(self.t, t) - 1, 0, len(self.t) - 2))
        dt = self.t[i + 1] - self.t[i]
        f = (t - self.t[i]) / dt
        alt = self.alt[i] + f * (self.alt[i + 1] - self.alt[i])
        az = self.az[i] + f * (self.az[i + 1] - self.az[i])
        return float(alt), float(az % 360.0), float((self.alt[i + 1] - self.alt[i]) / dt), float(
            (self.az[i + 1] - self.az[i]) / dt
        )


@dataclass
class _Axes:
    alt: float = 0.0
    az: float = 0.0


class MountController:
    def __init__(self, engine: AstronomyEngine, driver: MountDriver, config: AppConfig, sim_time: float) -> None:
        self.engine = engine
        self.driver = driver
        self.config = config
        self.limits = config.effective_limits()
        self._t = sim_time
        self._epoch = 0
        self.mode = Mode.DISCONNECTED
        self.target: Target | None = None
        self.track: TargetTrack | None = None
        self.track_after_slew = True
        self.events: deque[dict[str, Any]] = deque(maxlen=60)
        self.offset = _Axes()  # pointing-model correction (mount = sky + offset)
        self._backlash_dir = _Axes(1.0, 1.0)
        self._az_cmd: float | None = None
        self._settle = 0
        self._manual_expires = 0.0
        self._manual_rates = (0.0, 0.0)
        self._last_sun_check = -1e18
        self._sun_ok = True
        self.flags: set[str] = set()
        self.last = _Axes()
        self.last_target_pos: tuple[float, float, float, float] | None = None
        self.tracking_error: float | None = None
        self._status: DriverStatus | None = None
        self.connect()

    # ----------------------------------------------------------- utilities
    def log(self, msg: str, level: str = "info") -> None:
        self.events.append({"t": self._t, "level": level, "msg": msg})

    @property
    def status_cache(self) -> DriverStatus:
        if self._status is None:
            self._status = self.driver.status()
        return self._status

    def connect(self) -> None:
        try:
            self.driver.connect()
            self.mode = Mode.IDLE
            self.log(f"Connected to {self.driver.capabilities().name}")
        except Exception as exc:  # pragma: no cover - depends on hardware
            self.mode = Mode.DISCONNECTED
            self.log(f"Connection failed: {exc}", "error")

    def apply_config(self, config: AppConfig) -> None:
        self.config = config
        self.limits = config.effective_limits()
        self.engine.set_observer(config.observer)
        if self.track:
            self.track.invalidate()
        reconf = getattr(self.driver, "reconfigure", None)
        if reconf:
            reconf(self.limits, config.sim)

    def _require_operational(self) -> None:
        st = self.driver.status()
        if not st.connected:
            raise CommandError("mount is not connected")
        if st.estop:
            raise CommandError("emergency stop is latched; reset it first")
        if [f for f in st.faults if f != "estop"]:
            raise CommandError(f"mount has active faults: {', '.join(st.faults)}; reset first")
        if self.mode == Mode.HOMING:
            raise CommandError("homing in progress")

    def _sky_from_mount(self, st: DriverStatus) -> tuple[float, float]:
        """Best estimate of where the payload points (sky alt/az) from the mount's own sensors."""
        if st.alt.encoder is not None and st.az.encoder is not None:
            alt_m, az_m = st.alt.encoder, st.az.encoder
        else:
            comp = self.config.control.backlash_compensation
            b_alt = self.config.sim.backlash_alt / 2 if comp else 0.0
            b_az = self.config.sim.backlash_az / 2 if comp else 0.0
            alt_m = st.alt.position - self._backlash_dir.alt * b_alt
            az_m = st.az.position - self._backlash_dir.az * b_az
        return alt_m - self.offset.alt, (az_m - self.offset.az) % 360.0

    def _sun_separation_at(self, alt: float, az: float, t: float) -> float:
        s_alt, s_az = self.engine.sun_altaz(t)
        return separation(alt, az, float(s_alt), float(s_az))

    def _solar_policy_allows(self) -> bool:
        return self.config.control.allow_solar_targets and self.driver.capabilities().simulated

    # ------------------------------------------------------------- commands
    def goto(self, target_id: str, track: bool | None = None) -> dict[str, Any]:
        self._require_operational()
        try:
            target = self.engine.resolve(target_id)
        except TargetError as exc:
            raise CommandError(str(exc)) from exc
        ctl = self.config.control
        if target.is_sun and not self._solar_policy_allows():
            raise CommandError("automatic slewing to the Sun is disabled (solar safety); see docs/safety.md")
        alt, az, _, _ = self.engine.altaz_rates(target, self._t)
        if alt < ctl.min_target_altitude and target.kind != "altaz":
            raise CommandError(f"{target.name} is below the altitude limit ({alt:.1f}° < {ctl.min_target_altitude:.1f}°)")
        if target.kind == "altaz" and not (self.limits.min_altitude <= alt <= self.limits.max_altitude):
            raise CommandError("requested altitude is outside the mechanical limits")
        if alt > self.limits.max_altitude:
            raise CommandError(
                f"{target.name} is above the payload's reachable altitude ({alt:.1f}° > {self.limits.max_altitude:.1f}°)"
            )
        if not self._solar_policy_allows():
            sep = self.engine.sun_separation(target, self._t)
            if sep < ctl.sun_exclusion_deg:
                raise CommandError(
                    f"{target.name} is {sep:.1f}° from the Sun (exclusion zone {ctl.sun_exclusion_deg:.0f}°)"
                )
            self._check_path_avoids_sun(alt, az)
        st = self.driver.status()
        az_cmd = nearest_unwrapped(az + self.offset.az, st.az.position, self.limits.az_min, self.limits.az_max)
        if az_cmd is None:
            raise CommandError("azimuth not reachable within cable-wrap limits")
        self.target = target
        self.track = TargetTrack(self.engine, target)
        self._az_cmd = az_cmd
        self.track_after_slew = ctl.track_after_goto if track is None else track
        if target.kind == "altaz":
            self.track_after_slew = False
        self.mode = Mode.SLEWING
        self._settle = 0
        self.flags.clear()
        self.log(f"GoTo {target.name} (alt {alt:.2f}°, az {az:.2f}°)")
        return {"ok": True, "message": f"Slewing to {target.name}"}

    def _check_path_avoids_sun(self, alt: float, az: float) -> None:
        st = self.driver.status()
        cur_alt, cur_az = self._sky_from_mount(st)
        s_alt, s_az = (float(x) for x in self.engine.sun_altaz(self._t))
        if s_alt < -self.config.control.sun_exclusion_deg:
            return
        daz = wrap180(az - cur_az)
        for f in np.linspace(0.0, 1.0, 41):
            if separation(cur_alt + f * (alt - cur_alt), cur_az + f * daz, s_alt, s_az) < self.config.control.sun_exclusion_deg:
                raise CommandError("the slew path would cross the solar exclusion zone; move away from the Sun manually first")

    def start_tracking(self) -> dict[str, Any]:
        if self.target is None:
            raise CommandError("no target selected")
        if self.target.kind == "altaz":
            raise CommandError("fixed horizon positions are not tracked")
        return self.goto(self.target.id, track=True)

    def stop_tracking(self) -> dict[str, Any]:
        if self.mode in (Mode.TRACKING, Mode.SLEWING):
            self.driver.stop()
            self.mode = Mode.IDLE
            self.log("Tracking stopped")
        return {"ok": True, "message": "Tracking stopped"}

    def stop(self) -> dict[str, Any]:
        self.driver.stop()
        if self.mode not in (Mode.STOPPED, Mode.FAULT, Mode.DISCONNECTED):
            self.mode = Mode.IDLE
        self.flags.discard("unwinding")
        self.log("Stop")
        return {"ok": True, "message": "Stopping"}

    def emergency_stop(self) -> dict[str, Any]:
        self.driver.emergency_stop()
        self.mode = Mode.STOPPED
        self.log("EMERGENCY STOP (software). Use the hardware E-stop on physical mounts.", "error")
        return {"ok": True, "message": "Emergency stop engaged"}

    def reset(self) -> dict[str, Any]:
        self.driver.reset()
        if not self.driver.status().connected:
            try:
                self.driver.connect()
            except Exception as exc:
                self.mode = Mode.DISCONNECTED
                raise CommandError(f"reconnect failed: {exc}") from exc
        self.mode = Mode.IDLE
        self.flags.clear()
        self.log("Faults / emergency stop reset")
        return {"ok": True, "message": "Reset"}

    def manual(self, alt_rate: float, az_rate: float, wall_time: float) -> dict[str, Any]:
        self._require_operational()
        if not all(math.isfinite(x) for x in (alt_rate, az_rate)):
            raise CommandError("rates must be finite numbers")
        vmax = self.limits.max_rate
        alt_rate = max(-vmax, min(vmax, alt_rate))
        az_rate = max(-vmax, min(vmax, az_rate))
        if alt_rate == 0.0 and az_rate == 0.0:
            if self.mode == Mode.MANUAL:
                self.driver.stop()
                self.mode = Mode.IDLE
            return {"ok": True, "message": "Manual motion stopped"}
        if self.mode != Mode.MANUAL:
            self.log("Manual control")
        self.mode = Mode.MANUAL
        self._manual_rates = (alt_rate, az_rate)
        self._manual_expires = wall_time + self.config.control.manual_deadman_s
        self.driver.set_rates(alt_rate, az_rate)
        return {"ok": True, "message": "Manual slew"}

    def home(self) -> dict[str, Any]:
        self._require_operational()
        self.driver.home()
        self.mode = Mode.HOMING
        self.target = None
        self.log("Homing")
        return {"ok": True, "message": "Homing"}

    def sync(self, target_id: str) -> dict[str, Any]:
        """Declare that the payload currently points at ``target_id`` (alignment)."""
        self._require_operational()
        target = self.engine.resolve(target_id)
        alt, az, _, _ = self.engine.altaz_rates(target, self._t)
        st = self.driver.status()
        m_alt = st.alt.encoder if st.alt.encoder is not None else st.alt.position
        m_az = st.az.encoder if st.az.encoder is not None else st.az.position
        self.offset = _Axes(m_alt - alt, wrap180(m_az - az))
        self.log(f"Synced on {target.name}: offset alt {self.offset.alt:+.3f}°, az {self.offset.az:+.3f}°")
        return {"ok": True, "message": "Synced", "offset": {"alt": self.offset.alt, "az": self.offset.az}}

    def apply_correction(self, d_alt: float, d_az: float) -> dict[str, Any]:
        """Add a measured pointing correction (e.g. from plate solving) to the model."""
        self.offset = _Axes(self.offset.alt + d_alt, self.offset.az + d_az)
        self.log(f"Pointing correction applied: alt {d_alt:+.3f}°, az {d_az:+.3f}°")
        return {"ok": True, "offset": {"alt": self.offset.alt, "az": self.offset.az}}

    def clear_sync(self) -> dict[str, Any]:
        self.offset = _Axes()
        self.log("Pointing model cleared")
        return {"ok": True}

    # --------------------------------------------------------------- update
    def update(self, sim_time: float, wall_time: float, epoch: int = 0) -> None:
        if epoch != self._epoch:
            self._epoch = epoch
            self._time_jump(sim_time)
            return
        span = sim_time - self._t
        if span <= 0:
            if span < 0:
                self._time_jump(sim_time)
            return
        steady = self.mode == Mode.TRACKING and not self.flags and (self.tracking_error or 1.0) < 0.01
        n = min(MAX_CONTROL_STEPS, max(1, math.ceil(span / (STEADY_CONTROL_DT if steady else CONTROL_DT) - 1e-9)))
        step = span / n
        if self.track is not None and self.mode in (Mode.SLEWING, Mode.TRACKING):
            self.track.ensure(self._t, sim_time)
        t_prev = self._t
        for i in range(1, n + 1):
            t = self._t + i * step if i < n else sim_time
            # Setpoints are computed for the driver's current instant (start of the interval);
            # the (position, rate) setpoint is then extrapolated by the axis while it runs.
            self._control_step(t_prev, wall_time)
            self.driver.update(t)
            t_prev = t
        self._t = sim_time
        self._status = self.driver.status()

    def _time_jump(self, sim_time: float) -> None:
        self.log("Time discontinuity: re-planning", "warning")
        self._t = sim_time
        self.driver.update(sim_time)
        if self.track:
            self.track.invalidate()
        if self.mode == Mode.TRACKING and self.target:
            self.mode = Mode.SLEWING
            self._settle = 0
            st = self.driver.status()
            alt, az, _, _ = self.track.at(sim_time) if self.track else (0, 0, 0, 0)
            if alt < self.config.control.min_target_altitude:
                self.driver.stop()
                self.mode = Mode.IDLE
                self.log(f"{self.target.name} is below the horizon at the new time; tracking stopped", "warning")
            else:
                self._az_cmd = nearest_unwrapped(az + self.offset.az, st.az.position, self.limits.az_min, self.limits.az_max)
        self._status = self.driver.status()

    def _control_step(self, t: float, wall: float) -> None:
        st = self.driver.status()
        self._status = st
        if not st.connected:
            if self.mode != Mode.DISCONNECTED:
                self.log("Connection to mount lost; mount firmware should stop safely", "error")
            self.mode = Mode.DISCONNECTED
            return
        if self.mode == Mode.DISCONNECTED:
            self.mode = Mode.IDLE
            self.log("Mount connection restored; previous motion commands discarded")
        if st.estop:
            self.mode = Mode.STOPPED
            return
        active_faults = [f for f in st.faults if f != "estop"]
        if active_faults:
            if self.mode != Mode.FAULT:
                self.log(f"Fault: {', '.join(active_faults)}; motion halted", "error")
                self.driver.stop()
            self.mode = Mode.FAULT
            return
        if self.mode == Mode.HOMING:
            if st.homed and not st.homing and not st.alt.moving and not st.az.moving:
                self.mode = Mode.IDLE
                self.log("Homing complete")
            return
        if self.mode == Mode.MANUAL:
            self._manual_step(st, t, wall)
            return
        if self.mode in (Mode.SLEWING, Mode.TRACKING) and self.target and self.track:
            self._goto_step(st, t)

    def _manual_step(self, st: DriverStatus, t: float, wall: float) -> None:
        if wall > self._manual_expires:
            self.driver.stop()
            self.mode = Mode.IDLE
            self.log("Manual slew stopped (no refresh from controlling client)")
            return
        ar, zr = self._manual_rates
        a = self.limits.max_accel
        new_ar, new_zr = ar, zr
        # Soft limits: brake early enough to stop before the limit.
        if ar > 0 and st.alt.position + st.alt.velocity**2 / (2 * a) + 0.05 >= self.limits.max_altitude:
            new_ar = 0.0
        if ar < 0 and st.alt.position - st.alt.velocity**2 / (2 * a) - 0.05 <= self.limits.min_altitude:
            new_ar = 0.0
        if zr > 0 and st.az.position + st.az.velocity**2 / (2 * a) + 0.05 >= self.limits.az_max:
            new_zr = 0.0
        if zr < 0 and st.az.position - st.az.velocity**2 / (2 * a) - 0.05 <= self.limits.az_min:
            new_zr = 0.0
        if not self._solar_policy_allows() and t - self._last_sun_check >= 1.0:
            self._last_sun_check = t
            alt, az = self._sky_from_mount(st)
            sep = self._sun_separation_at(alt, az, t)
            if sep < self.config.control.sun_exclusion_deg:
                # Allow only motion that increases the separation.
                s_alt, s_az = (float(x) for x in self.engine.sun_altaz(t))
                ahead = separation(alt + ar * 1.0, az + zr * 1.0, s_alt, s_az)
                if ahead <= sep:
                    new_ar, new_zr = 0.0, 0.0
                    self.log("Manual slew blocked at the solar exclusion zone", "warning")
        if (new_ar, new_zr) != (ar, zr):
            self._manual_rates = (new_ar, new_zr)
            if new_ar == 0.0 and new_zr == 0.0:
                self.driver.stop()
                self.mode = Mode.IDLE
                self.log("Manual slew stopped at a limit", "warning")
            else:
                self.driver.set_rates(new_ar, new_zr)

    def _goto_step(self, st: DriverStatus, t: float) -> None:
        assert self.target is not None and self.track is not None
        ctl = self.config.control
        lim = self.limits
        alt, az, ar, zr = self.track.at(t)
        self.last_target_pos = (alt, az, ar, zr)
        if self.target.kind != "altaz" and alt < ctl.min_target_altitude:
            self.driver.stop()
            self.mode = Mode.IDLE
            self.log(f"{self.target.name} went below the altitude limit; tracking stopped", "warning")
            return
        if not self._solar_policy_allows() and t - self._last_sun_check >= SUN_CHECK_INTERVAL:
            self._last_sun_check = t
            if self._sun_separation_at(alt, az, t) < ctl.sun_exclusion_deg:
                self.driver.stop()
                self.mode = Mode.IDLE
                self.log("Target entered the solar exclusion zone; tracking stopped", "warning")
                return
        # Mechanical altitude reach (payload-dependent).
        cmd_alt = alt + self.offset.alt
        if cmd_alt > lim.max_altitude or cmd_alt < lim.min_altitude:
            self.flags.add("out_of_reach")
            cmd_alt = max(lim.min_altitude, min(lim.max_altitude, cmd_alt))
            ar = 0.0
        else:
            self.flags.discard("out_of_reach")
        # Multi-turn azimuth with cable-wrap limits.
        az_m = az + self.offset.az
        if self._az_cmd is None:
            self._az_cmd = nearest_unwrapped(az_m, st.az.position, lim.az_min, lim.az_max) or 0.0
        az_cmd = self._az_cmd + wrap180(az_m - self._az_cmd)
        if not lim.az_min <= az_cmd <= lim.az_max:
            alt_cand = az_cmd - 360.0 if az_cmd > lim.az_max else az_cmd + 360.0
            if ctl.auto_unwind and lim.az_min <= alt_cand <= lim.az_max:
                az_cmd = alt_cand
                self.mode = Mode.SLEWING
                self._settle = 0
                self.flags.add("unwinding")
                self.log("Cable-wrap limit reached: unwinding azimuth by 360°", "warning")
            else:
                self.driver.stop()
                self.mode = Mode.IDLE
                self.log("Cable-wrap limit reached; tracking stopped", "warning")
                return
        self._az_cmd = az_cmd
        # Backlash compensation: approach so the gear slack is taken up in the motion direction.
        if self.mode == Mode.SLEWING:
            if abs(cmd_alt - st.alt.position) > 0.2:
                self._backlash_dir.alt = 1.0 if cmd_alt > st.alt.position else -1.0
            if abs(az_cmd - st.az.position) > 0.2:
                self._backlash_dir.az = 1.0 if az_cmd > st.az.position else -1.0
        if abs(ar) > 1e-5 and (self.mode == Mode.TRACKING or abs(cmd_alt - st.alt.position) <= 0.2):
            self._backlash_dir.alt = 1.0 if ar > 0 else -1.0
        if abs(zr) > 1e-5 and (self.mode == Mode.TRACKING or abs(az_cmd - st.az.position) <= 0.2):
            self._backlash_dir.az = 1.0 if zr > 0 else -1.0
        comp = ctl.backlash_compensation
        b_alt = self.config.sim.backlash_alt / 2 if comp else 0.0
        b_az = self.config.sim.backlash_az / 2 if comp else 0.0
        # Open-loop compensation with the calibrated backlash; encoders (if fitted) are used
        # to measure the resulting pointing and to detect lost steps.
        motor_alt = cmd_alt + self._backlash_dir.alt * b_alt
        motor_az = az_cmd + self._backlash_dir.az * b_az
        rate_cap = lim.tracking_max_rate
        if abs(zr) > rate_cap * 0.95 or abs(ar) > rate_cap * 0.95:
            if "rate_limited" not in self.flags:
                self.log("Required axis rate exceeds the limit (zenith keyhole); tracking will lag", "warning")
            self.flags.add("rate_limited")
        else:
            self.flags.discard("rate_limited")
        self.driver.move_to(motor_alt, motor_az, ar, zr)
        # Pointing error as seen by the controller.
        est_alt, est_az = self._sky_from_mount(st)
        err = separation(alt, az, est_alt, est_az)
        self.tracking_error = err
        speed_ok = abs(st.alt.velocity - ar) < 0.05 and abs(st.az.velocity - zr) < 0.05
        if self.mode == Mode.SLEWING:
            tol = ctl.goto_tolerance_deg
            if err < tol and speed_ok:
                self._settle += 1
                if self._settle >= SETTLE_STEPS:
                    self.flags.discard("unwinding")
                    if self.track_after_slew:
                        self.mode = Mode.TRACKING
                        self.log(f"Arrived at {self.target.name}; tracking (error {err * 3600:.0f}″)")
                    else:
                        self.mode = Mode.IDLE
                        if self.target.kind != "altaz":
                            self.driver.stop()
                        self.log(f"Arrived at {self.target.name} (error {err * 3600:.0f}″)")
            else:
                self._settle = 0
        elif self.mode == Mode.TRACKING and err > REACQUIRE_DEG and "rate_limited" not in self.flags \
                and "out_of_reach" not in self.flags:
            self.mode = Mode.SLEWING
            self._settle = 0
            self.log(f"Tracking error {err:.2f}° too large: re-acquiring", "warning")

    # --------------------------------------------------------------- status
    def snapshot(self, with_radec: bool = True) -> dict[str, Any]:
        st = self.status_cache
        est_alt, est_az = self._sky_from_mount(st)
        pos: dict[str, Any] = {
            "alt": est_alt,
            "az": est_az,
            "alt_axis": st.alt.position,
            "az_axis": st.az.position,
            "alt_rate": st.alt.velocity,
            "az_rate": st.az.velocity,
        }
        if with_radec:
            rd = self.engine.altaz_to_radec(est_alt, est_az, self._t)
            pos["ra"], pos["dec"] = rd["ra_j2000"], rd["dec_j2000"]
        target: dict[str, Any] | None = None
        true_err = None
        if self.target is not None:
            if self.track is not None and self.mode in (Mode.SLEWING, Mode.TRACKING):
                alt, az, ar, zr = self.track.at(self._t)
            else:
                alt, az, ar, zr = self.engine.altaz_rates(self.target, self._t)
            target = {**self.target.summary(), "alt": alt, "az": az, "alt_rate": ar, "az_rate": zr}
            if st.truth:
                true_err = separation(alt, az, st.truth["alt"], st.truth["az"])
            if self.mode not in (Mode.SLEWING, Mode.TRACKING):
                self.tracking_error = separation(alt, az, est_alt, est_az)
        return {
            "time": self._t,
            "mode": self.mode,
            "tracking": self.mode == Mode.TRACKING,
            "target": target,
            "position": pos,
            "tracking_error": self.tracking_error if self.target else None,
            "true_pointing_error": true_err,
            "flags": sorted(self.flags),
            "field_rotation_rate": field_rotation_rate(self.config.observer.latitude, est_alt, est_az),
            "pointing_offset": {"alt": self.offset.alt, "az": self.offset.az},
            "driver": {
                "connected": st.connected,
                "homed": st.homed,
                "homing": st.homing,
                "estop": st.estop,
                "faults": st.faults,
                "alt_encoder": st.alt.encoder,
                "az_encoder": st.az.encoder,
                "alt_moving": st.alt.moving,
                "az_moving": st.az.moving,
                "motor": st.motor,
                "truth": st.truth,
            },
            "limits": {
                "alt_min": self.limits.min_altitude,
                "alt_max": self.limits.max_altitude,
                "az_min": self.limits.az_min,
                "az_max": self.limits.az_max,
                "max_rate": self.limits.max_rate,
                "max_accel": self.limits.max_accel,
            },
            "events": list(self.events)[-15:],
        }
