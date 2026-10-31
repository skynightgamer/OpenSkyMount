from __future__ import annotations

import datetime as dt

import pytest

from openskymount.astro.engine import AstronomyEngine
from openskymount.config import AppConfig, ObserverConfig
from openskymount.mount.controller import MountController
from openskymount.mount.sim_driver import SimulatedMountDriver

# 2026-11-01 01:00 UTC: Jupiter well up in the east from Berlin, night time.
T0 = dt.datetime(2026, 11, 1, 1, 0, tzinfo=dt.timezone.utc).timestamp()


@pytest.fixture(scope="session")
def engine() -> AstronomyEngine:
    return AstronomyEngine(ObserverConfig())


class Rig:
    """Controller + simulated driver advanced with explicit time steps."""

    def __init__(self, cfg: AppConfig, t0: float = T0) -> None:
        self.cfg = cfg
        self.engine = AstronomyEngine(cfg.observer)
        self.driver = SimulatedMountDriver(cfg.effective_limits(), cfg.sim, t0)
        self.ctl = MountController(self.engine, self.driver, cfg, t0)
        self.t = t0
        self.wall = 0.0
        self.epoch = 0

    def run(self, seconds: float, dt: float = 0.25, wall_dt: float | None = None) -> dict:
        end = self.t + seconds
        while self.t < end - 1e-9:
            step = min(dt, end - self.t)
            self.t += step
            self.wall += step if wall_dt is None else wall_dt
            self.ctl.update(self.t, self.wall, self.epoch)
        return self.ctl.snapshot(with_radec=False)

    def jump(self, new_t: float) -> dict:
        self.t = new_t
        self.epoch += 1
        self.ctl.update(self.t, self.wall, self.epoch)
        return self.ctl.snapshot(with_radec=False)


@pytest.fixture
def rig() -> Rig:
    return Rig(AppConfig())


@pytest.fixture
def make_rig():
    return Rig
