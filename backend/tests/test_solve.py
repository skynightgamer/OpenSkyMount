"""Plate-solving proof of concept (skipped if the optional solver is not installed)."""

from __future__ import annotations

import math

import pytest

from openskymount.astro.catalog import load_catalog
from openskymount.config import AppConfig
from openskymount.mount.controller import Mode
from openskymount.solve import render_star_field, simulated_alignment, solve_image, solver_available

from .conftest import Rig

pytestmark = pytest.mark.skipif(not solver_available(), reason="cedar-solve not installed")


def sep_arcsec(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    r = math.radians
    c = math.sin(r(dec1)) * math.sin(r(dec2)) + math.cos(r(dec1)) * math.cos(r(dec2)) * math.cos(r(ra1 - ra2))
    return math.degrees(math.acos(min(1.0, c))) * 3600


@pytest.mark.parametrize("ra,dec,roll", [(79.17, 45.99, 17), (279.23, 38.78, -40), (101.3, -16.7, 90), (10.68, 41.27, 0), (200.0, -60.0, 135)])
def test_solves_synthetic_fields(ra: float, dec: float, roll: float) -> None:
    sol = solve_image(render_star_field(load_catalog(), ra, dec, roll), fov_estimate=20.0)
    assert sol["solved"], sol
    assert sep_arcsec(sol["ra"], sol["dec"], ra, dec) < 90
    assert abs(((sol["roll"] - roll) + 180) % 360 - 180) < 0.5
    assert abs(sol["fov"] - 20.0) < 0.1


def test_alignment_loop_corrects_mount_errors(make_rig) -> None:
    cfg = AppConfig()
    cfg.sim.az_zero_offset = 0.6
    cfg.sim.alt_zero_offset = -0.35
    cfg.sim.tilt_magnitude = 0.2
    cfg.sim.tilt_direction = 120.0
    rig: Rig = make_rig(cfg)
    rig.ctl.goto("Capella")
    snap = rig.run(60)
    assert snap["mode"] == Mode.TRACKING
    before = snap["true_pointing_error"]
    assert before > 0.4
    result = simulated_alignment(rig.ctl)
    assert result["solution"]["solved"] and result["applied"]
    assert result["solve_vs_truth_arcsec"] < 90
    snap = rig.run(30)
    assert snap["true_pointing_error"] < 0.03, (before, snap["true_pointing_error"])
