"""Star suggestions for the pointing alignment, validated by how well they pin down a known mount error."""

from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from openskymount.config import AppConfig
from openskymount.mount.align_stars import MAX_MAG, MIN_ALT, suggest_alignment_stars
from openskymount.mount.geometry import separation
from openskymount.mount.pointing import AlignmentPoint, PointingModel
from openskymount.server.app import Observatory, create_app

from .conftest import T0, Rig

# A mount that is badly set up: offsets of a few tenths of a degree and a base tilt of half a degree.
TRUTH = dict(alt_off=0.25, az_off=-0.6, tilt_n=0.4, tilt_e=-0.3)
CENTRING_NOISE_DEG = 0.03  # how well a person centres a star in the payload


def fit_error(stars: list[tuple[float, float]], rng: np.random.Generator) -> float:
    """Worst error (deg) of the fitted model parameters after aligning on ``stars`` (sky alt, az) with centring noise."""
    truth = PointingModel(**TRUTH)
    model = PointingModel()
    for alt, az in stars:
        m_alt, m_az = truth.sky_to_mount(alt, az)
        # The person centres the star by eye: the mount reading is the true one plus a small random error.
        model.add(AlignmentPoint(m_alt + rng.normal(0, CENTRING_NOISE_DEG), m_az + rng.normal(0, CENTRING_NOISE_DEG), alt, az))
    got = np.array([model.alt_off, model.az_off, model.tilt_n, model.tilt_e])
    return float(np.max(np.abs(got - np.array(list(TRUTH.values())))))


def test_suggested_stars_are_bright_high_enough_clear_of_the_zenith_and_the_sun() -> None:
    rig = Rig(AppConfig())
    res = suggest_alignment_stars(rig.ctl, 3)
    assert res["dark"] and res["message"] is None and len(res["stars"]) == 3
    limit = rig.ctl.limits.max_altitude
    for s in res["stars"]:
        assert s["magnitude"] <= MAX_MAG
        assert MIN_ALT <= s["alt"] <= min(75.0, limit - 15.0)
        # Cross-check the position with the engine (the suggestion uses a fast rotation of catalog positions).
        alt, az = rig.engine.altaz(rig.engine.resolve(s["id"]), T0)
        assert separation(s["alt"], s["az"], float(alt), float(az)) < 0.4
    assert res["min_separation_deg"] > 60.0 and res["largest_azimuth_gap_deg"] < 160.0
    for count in (3, 5, 6):  # never a star within 10 degrees of a pole (Polaris)
        for s in suggest_alignment_stars(rig.ctl, count)["stars"]:
            assert abs(float(rig.engine.radec(rig.engine.resolve(s["id"]), T0)["dec_j2000"])) <= 80.0


def test_order_is_a_short_tour_from_where_the_mount_points() -> None:
    rig = Rig(AppConfig())
    stars = suggest_alignment_stars(rig.ctl, 4)["stars"]
    here = rig.ctl.pointing()
    first = min(stars, key=lambda s: separation(*here, s["alt"], s["az"]))
    assert stars[0]["id"] == first["id"]


@pytest.mark.parametrize("count", [3, 5])
def test_suggested_stars_recover_a_tilted_mount_better_than_clustered_stars(count: int) -> None:
    """Independent check of the choice: same centring noise, the spread stars give a much better model."""
    rig = Rig(AppConfig())
    spread = [(s["alt"], s["az"]) for s in suggest_alignment_stars(rig.ctl, count)["stars"]]
    # The bright stars of the Plough are a typical clustered choice (all within about 25 degrees).
    plough = [rig.engine.resolve(f"hip:{h}") for h in (54061, 53910, 58001, 59774, 62956)[:count]]
    clustered = [tuple(float(x) for x in rig.engine.altaz(t, T0)) for t in plough]
    assert all(a > 10 for a, _ in clustered)
    rng = np.random.default_rng(7)
    good = np.mean([fit_error(spread, rng) for _ in range(300)])
    bad = np.mean([fit_error(clustered, rng) for _ in range(300)])
    assert good < 0.5 * bad, (good, bad)
    assert good < 0.06  # a few hundredths of a degree, comparable with the centring noise


def test_already_used_stars_are_not_suggested_again() -> None:
    rig = Rig(AppConfig())
    first = suggest_alignment_stars(rig.ctl, 3)["stars"]
    rig.ctl.goto(first[0]["id"], track=False)
    rig.run(90.0)
    rig.ctl.sync(first[0]["id"])
    again = suggest_alignment_stars(rig.ctl, 3)
    assert first[0]["name"] not in {s["name"] for s in again["stars"]}


def test_daylight_is_reported_and_too_few_stars_get_a_message() -> None:
    rig = Rig(AppConfig())
    rig.jump(T0 + 12 * 3600)  # noon in Berlin
    day = suggest_alignment_stars(rig.ctl, 3)
    assert not day["dark"] and day["sun_alt"] > 0 and len(day["stars"]) in (0, 3)
    # Few stars clear of the limits: ask for more than exist.
    rig2 = Rig(AppConfig())
    few = suggest_alignment_stars(rig2.ctl, 6)
    assert len(few["stars"]) <= 6
    # A payload that cannot point high leaves a narrow altitude window: fewer stars than asked for, with an explanation.
    cfg = AppConfig()
    cfg.limits.max_altitude = 42.0  # the window is only 25 to 27 degrees
    narrow = suggest_alignment_stars(Rig(cfg).ctl, 6)
    assert len(narrow["stars"]) < 6 and narrow["message"] and "suitable" in narrow["message"]
    cfg.limits.max_altitude = 40.0  # no window at all
    none = suggest_alignment_stars(Rig(cfg).ctl, 3)
    assert none["stars"] == [] and "No suitable" in none["message"]


def test_endpoint(tmp_path) -> None:
    with TestClient(create_app(observatory=Observatory(state_dir=tmp_path))) as c:
        d = c.get("/api/align_stars", params={"count": 3}).json()
        assert d["requested"] == 3 and len(d["stars"]) <= 3
        assert all({"id", "name", "alt", "az", "magnitude"} <= set(s) for s in d["stars"])
        assert c.get("/api/align_stars", params={"count": 9}).status_code == 422


def test_aligning_on_the_suggested_stars_corrects_the_whole_simulated_sky() -> None:
    """In the loop with the simulated mount (offsets and a tilted base): after the three suggested stars, held-out stars
    are hit within 72 arc seconds. (The noiseless simulator does not separate good from poor star choices; the noisy
    recovery test above does.)"""
    from .test_pointing import align_on, errors_at, misaligned, spread_targets

    rig = Rig(misaligned())
    suggested = [s["id"] for s in suggest_alignment_stars(rig.ctl, 3)["stars"]]
    for tid in suggested:
        align_on(rig, tid)
    held_out = [t for t in spread_targets(rig, 8) if t not in suggested][:4]
    raw = errors_at(Rig(misaligned()), held_out)
    assert max(raw) > 0.4  # without alignment the mount misses by a lot
    assert max(errors_at(rig, held_out)) < 0.02
