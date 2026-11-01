"""Multi-star pointing model: fitting and sky-wide accuracy in the simulator."""

from __future__ import annotations

import itertools

import numpy as np
import pytest

from openskymount.config import AppConfig
from openskymount.mount.controller import Mode
from openskymount.mount.geometry import separation
from openskymount.mount.pointing import AlignmentPoint, PointingModel

from .conftest import Rig

TRUE = {"alt_off": -0.35, "az_off": 0.6, "tilt_n": 0.21, "tilt_e": -0.12}


def synthetic_points(n: int, seed: int = 3) -> list[AlignmentPoint]:
    truth = PointingModel(**TRUE)
    rng = np.random.default_rng(seed)
    pts = []
    for _ in range(n):
        m_alt, m_az = rng.uniform(15, 75), rng.uniform(0, 360)
        s_alt, s_az = truth.mount_to_sky(m_alt, m_az)
        pts.append(AlignmentPoint(m_alt, m_az, s_alt, s_az))
    return pts


def test_mapping_round_trip() -> None:
    m = PointingModel(**TRUE)
    for alt, az in itertools.product((5, 30, 60, 85), (0, 90, 181, 359)):
        s_alt, s_az = m.mount_to_sky(alt, az)
        b_alt, b_az = m.sky_to_mount(s_alt, s_az)
        assert separation(alt, az, b_alt, b_az) < 1e-9


def test_fit_recovers_full_model_from_three_or_more_points() -> None:
    for n in (3, 6):
        m = PointingModel(points=synthetic_points(n))
        m.fit()
        assert m.terms == "offsets + base tilt"
        for key, value in TRUE.items():
            assert abs(getattr(m, key) - value) < 1e-6, (n, key)
        assert m.rms() is not None and m.rms() < 1e-6


def test_one_or_two_points_fit_offsets_only() -> None:
    pure = PointingModel(alt_off=0.4, az_off=-0.7)
    pts = []
    for m_alt, m_az in ((40, 30), (55, 200)):
        s_alt, s_az = pure.mount_to_sky(m_alt, m_az)
        pts.append(AlignmentPoint(m_alt, m_az, s_alt, s_az))
    m = PointingModel(points=pts[:1])
    m.fit()
    assert m.terms == "zero offsets" and m.tilt_n == 0 and m.tilt_e == 0
    assert abs(m.alt_off - 0.4) < 1e-6 and abs(m.az_off + 0.7) < 1e-6
    m = PointingModel(points=pts)
    m.fit()
    assert abs(m.alt_off - 0.4) < 1e-6 and abs(m.az_off + 0.7) < 1e-6


def test_noisy_points_report_residuals() -> None:
    pts = synthetic_points(5)
    rng = np.random.default_rng(1)
    for p in pts:
        p.sky_alt += rng.normal(0, 20 / 3600)
        p.sky_az += rng.normal(0, 20 / 3600)
    m = PointingModel(points=pts)
    m.fit()
    assert 5 / 3600 < m.rms() < 40 / 3600
    assert len(m.describe()["points"]) == 5


# ------------------------------------------------------------ simulator
def misaligned() -> AppConfig:
    cfg = AppConfig()
    cfg.sim.az_zero_offset = TRUE["az_off"]
    cfg.sim.alt_zero_offset = TRUE["alt_off"]
    cfg.sim.tilt_magnitude = 0.3
    cfg.sim.tilt_direction = 120.0
    return cfg


def spread_targets(rig: Rig, count: int, min_alt: float = 25.0) -> list[str]:
    """Bright named stars above ``min_alt`` with azimuths as far apart as possible."""
    cands = []
    for s in rig.engine.catalog.stars[:150]:
        if not s["name"]:
            continue
        tid = f"hip:{s['hip']}"
        alt, az = rig.engine.altaz(rig.engine.resolve(tid), rig.t)
        if min_alt < float(alt) < 80:
            cands.append((float(az), tid))
    cands.sort()
    step = len(cands) / count
    return [cands[int(i * step)][1] for i in range(count)]


def align_on(rig: Rig, tid: str) -> None:
    """GoTo a star, then record where the payload truly points (what a plate solve measures)."""
    rig.ctl.goto(tid, track=True)
    snap = rig.run(60)
    assert snap["mode"] == Mode.TRACKING
    truth = snap["driver"]["truth"]
    rig.ctl.add_alignment_point(truth["alt"], truth["az"], tid, "test")


def errors_at(rig: Rig, targets: list[str]) -> list[float]:
    out = []
    for tid in targets:
        rig.ctl.goto(tid)
        snap = rig.run(60)
        out.append(snap["true_pointing_error"])
    return out


def test_three_star_model_corrects_whole_sky(make_rig) -> None:
    rig: Rig = make_rig(misaligned())
    targets = spread_targets(rig, 7)
    align_stars, check_stars = targets[::2][:3], [t for t in targets if t not in targets[::2][:3]]
    raw = errors_at(rig, check_stars)
    assert max(raw) > 0.4  # uncalibrated mount misses badly

    align_on(rig, align_stars[0])
    one_star = errors_at(rig, check_stars)
    for tid in align_stars[1:]:
        align_on(rig, tid)
    model = rig.ctl.model.describe()
    assert model["terms"] == "offsets + base tilt" and len(model["points"]) == 3
    three_star = errors_at(rig, check_stars)

    assert max(one_star) > 0.1, one_star  # a single offset cannot fix the tilt everywhere
    assert max(three_star) < 0.02, three_star  # the 3-star model does (72")
    assert model["rms"] < 0.01


def test_sync_command_and_removal(rig: Rig) -> None:
    rig.ctl.goto("Capella")
    rig.run(60)
    res = rig.ctl.sync("Capella")
    assert res["ok"] and len(res["model"]["points"]) == 1
    res = rig.ctl.sync("Capella")  # re-centring the same object replaces its point
    assert len(res["model"]["points"]) == 1
    rig.ctl.remove_alignment_point(0)
    assert rig.ctl.model.terms == "none"
    with pytest.raises(Exception, match="index"):
        rig.ctl.remove_alignment_point(5)
