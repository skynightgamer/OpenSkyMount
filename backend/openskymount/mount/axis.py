"""Deterministic single-axis motion model.

Models a stepper-driven axis with:
* bounded velocity and acceleration (trapezoidal / time-optimal approach),
* position control with velocity feed-forward (for tracking moving targets),
* velocity mode (manual slewing), controlled stop and emergency stop,
* gear backlash between motor and output shaft,
* missed steps (stall): the step counter keeps counting while the shaft does not move,
* hard limit switches.

The same class is used by the in-process simulator and by the fake-firmware
endpoint, mirroring what real firmware would do on the microcontroller.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class AxisParams:
    max_rate: float = 6.0
    max_accel: float = 6.0
    kp: float = 3.0  # 1/s, proportional gain in the final approach
    backlash: float = 0.0
    hard_min: float = -1e9
    hard_max: float = 1e9


class AxisSim:
    IDLE = "idle"
    POSITION = "position"
    VELOCITY = "velocity"
    STOPPING = "stopping"

    def __init__(self, params: AxisParams, position: float = 0.0) -> None:
        self.p = params
        self.motor = position  # true motor-side angle (deg, output-referred)
        self.output = position  # true output-shaft angle after backlash
        self.count = position  # step-counter position (what firmware believes)
        self.vel = 0.0
        self.mode = self.IDLE
        self.target = position
        self.ff = 0.0
        self.vel_cmd = 0.0
        self.stalled = False
        self.limit_hit = False

    # ------------------------------------------------------------- commands
    def command_position(self, target: float, ff: float = 0.0) -> None:
        self.mode = self.POSITION
        self.target = target
        self.ff = ff

    def command_velocity(self, rate: float) -> None:
        self.mode = self.VELOCITY
        self.vel_cmd = max(-self.p.max_rate, min(self.p.max_rate, rate))

    def command_stop(self) -> None:
        if self.mode != self.IDLE:
            self.mode = self.STOPPING

    def hard_stop(self) -> None:
        self.vel = 0.0
        self.mode = self.IDLE

    def set_count(self, position: float) -> None:
        """Redefine the step counter (homing / sync)."""
        self.count = position
        self.target = position

    # ------------------------------------------------------------- physics
    def stopping_distance(self) -> float:
        return self.vel * self.vel / (2.0 * self.p.max_accel)

    def _desired_velocity(self) -> float:
        if self.mode == self.POSITION:
            e = self.target - self.count
            mag = min(math.sqrt(2.0 * self.p.max_accel * abs(e)), self.p.kp * abs(e))
            v = self.ff + math.copysign(mag, e)
        elif self.mode == self.VELOCITY:
            v = self.vel_cmd
        else:
            v = 0.0
        return max(-self.p.max_rate, min(self.p.max_rate, v))

    def step(self, dt: float) -> None:
        v_des = self._desired_velocity()
        dv_max = self.p.max_accel * dt
        v_new = self.vel + max(-dv_max, min(dv_max, v_des - self.vel))
        dx = 0.5 * (self.vel + v_new) * dt
        self.vel = v_new
        if self.mode == self.STOPPING and self.vel == 0.0:
            self.mode = self.IDLE
            self.target = self.count
        self.count += dx
        if not self.stalled:
            self.motor += dx
        half = self.p.backlash / 2.0
        if self.motor - self.output > half:
            self.output = self.motor - half
        elif self.output - self.motor > half:
            self.output = self.motor + half
        if not (self.p.hard_min <= self.output <= self.p.hard_max):
            self.output = max(self.p.hard_min, min(self.p.hard_max, self.output))
            self.motor = max(self.p.hard_min - half, min(self.p.hard_max + half, self.motor))
            self.limit_hit = True
            self.hard_stop()

    @property
    def moving(self) -> bool:
        return abs(self.vel) > 1e-9 or self.mode in (self.STOPPING, self.VELOCITY)
