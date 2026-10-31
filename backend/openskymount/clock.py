"""Observatory clock.

All astronomy and all simulated physics use *simulation time* (UTC seconds since
the Unix epoch, as a float). In live mode simulation time equals wall-clock UTC.
In simulated mode it advances at ``rate`` times wall-clock speed from an
arbitrary anchor, which allows accelerated tracking demonstrations.

Every change of anchor/rate increments ``epoch`` so that consumers can detect
time discontinuities.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

MAX_RATE = 3600.0


@dataclass
class ClockState:
    live: bool
    rate: float
    sim_time: float
    epoch: int


class SimClock:
    def __init__(self, wall: Callable[[], float] = time.time) -> None:
        self._wall = wall
        self._anchor_wall = wall()
        self._anchor_sim = self._anchor_wall
        self.rate = 1.0
        self.live = True
        self.paused = False
        self.epoch = 0

    def now(self) -> float:
        if self.live:
            return self._wall()
        if self.paused:
            return self._anchor_sim
        return self._anchor_sim + (self._wall() - self._anchor_wall) * self.rate

    def _reanchor(self, sim_time: float) -> None:
        self._anchor_wall = self._wall()
        self._anchor_sim = sim_time

    def set_live(self) -> None:
        self.live = True
        self.rate = 1.0
        self.paused = False
        self.epoch += 1

    def set_time(self, sim_time: float) -> None:
        self._reanchor(sim_time)
        self.live = False
        self.epoch += 1

    def set_rate(self, rate: float) -> None:
        if not (0.0 < rate <= MAX_RATE):
            raise ValueError(f"rate must be in (0, {MAX_RATE}]")
        now = self.now()
        self._reanchor(now)
        self.live = False
        self.rate = float(rate)
        # A pure rate change is continuous in time, so the epoch is unchanged.

    def set_paused(self, paused: bool) -> None:
        now = self.now()
        self._reanchor(now)
        self.live = False
        self.paused = paused

    def state(self) -> ClockState:
        return ClockState(live=self.live, rate=0.0 if self.paused else self.rate, sim_time=self.now(), epoch=self.epoch)


class ManualClock(SimClock):
    """Deterministic clock for tests: time only moves when ``advance`` is called."""

    def __init__(self, start: float) -> None:
        self._t = start
        super().__init__(wall=lambda: self._t)
        self.live = False

    def advance(self, dt: float) -> float:
        self._t += dt
        return self._t
