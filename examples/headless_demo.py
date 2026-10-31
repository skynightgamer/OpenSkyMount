#!/usr/bin/env python3
"""Reproducible headless demonstration (no browser, no network).

Goes to Jupiter from Berlin on 2026-11-01 01:00 UTC, tracks it for 3 simulated
hours, then shows a plate-solve alignment fixing a deliberately misaligned mount
(if the optional solver is installed).

    backend/.venv/bin/python examples/headless_demo.py
"""

import datetime as dt

from openskymount.astro.engine import AstronomyEngine
from openskymount.config import AppConfig
from openskymount.mount.controller import MountController
from openskymount.mount.sim_driver import SimulatedMountDriver

T0 = dt.datetime(2026, 11, 1, 1, 0, tzinfo=dt.timezone.utc).timestamp()


def rig(cfg: AppConfig):
    eng = AstronomyEngine(cfg.observer)
    drv = SimulatedMountDriver(cfg.effective_limits(), cfg.sim, T0)
    return MountController(eng, drv, cfg, T0)


def run(ctl, t, seconds, dt_s=0.25):
    end = t + seconds
    while t < end:
        t += dt_s
        ctl.update(t, t)
    return t, ctl.snapshot(with_radec=True)


def main() -> None:
    ctl = rig(AppConfig())
    print(ctl.goto("jupiter")["message"])
    t, s = run(ctl, T0, 40)
    print(f"after 40 s: mode={s['mode']} alt={s['position']['alt']:.3f} az={s['position']['az']:.3f} "
          f"true error={s['true_pointing_error'] * 3600:.1f}\"")
    for hour in range(1, 4):
        t, s = run(ctl, t, 3600, dt_s=10.0)
        print(f"+{hour} h: mode={s['mode']} target alt={s['target']['alt']:.2f} "
              f"true error={s['true_pointing_error'] * 3600:.1f}\" field rotation={s['field_rotation_rate'] * 3600:.1f}\"/s")

    try:
        from openskymount.solve import simulated_alignment, solver_available
    except ImportError:
        return
    if not solver_available():
        print("(plate-solving demo skipped: cedar-solve not installed)")
        return
    cfg = AppConfig()
    cfg.sim.az_zero_offset, cfg.sim.alt_zero_offset = 0.8, -0.5
    ctl = rig(cfg)
    ctl.goto("Capella")
    t, s = run(ctl, T0, 60)
    print(f"\nMisaligned mount on Capella: true error {s['true_pointing_error'] * 60:.1f}'")
    res = simulated_alignment(ctl)
    sol = res["solution"]
    print(f"plate solve: RA {sol['ra']:.4f} Dec {sol['dec']:.4f}, {sol['matches']} stars, "
          f"{res['solve_vs_truth_arcsec']:.0f}\" from truth")
    t, s = run(ctl, t, 30)
    print(f"after correction: true error {s['true_pointing_error'] * 3600:.0f}\"")


if __name__ == "__main__":
    main()
