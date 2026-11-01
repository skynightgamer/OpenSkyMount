"""Observing planner: darkness window and best targets."""

from __future__ import annotations

import datetime as dt

from openskymount.astro.engine import AstronomyEngine
from openskymount.astro.tonight import best_targets, dark_window
from openskymount.config import ObserverConfig

NOON = dt.datetime(2026, 11, 1, 12, tzinfo=dt.UTC).timestamp()


def test_dark_window_boundaries(engine: AstronomyEngine) -> None:
    start, end = dark_window(engine, NOON, -12.0)
    sun = lambda t: float(engine.sun_altaz(t)[0])  # noqa: E731
    assert NOON < start < end and 10 < (end - start) / 3600 < 14  # early-November night at 52.5° N
    assert sun(start) < -12.0 < sun(start - 120)
    assert sun(end - 120) < -12.0 < sun(end + 60)


def test_window_starts_now_when_already_dark(engine: AstronomyEngine) -> None:
    t = NOON + 10 * 3600  # 22:00 UTC
    start, end = dark_window(engine, t, -12.0)
    assert start == t and end > t


def test_polar_summer_has_no_dark_window() -> None:
    eng = AstronomyEngine(ObserverConfig(latitude=78.2, longitude=15.6, name="Longyearbyen"))
    t = dt.datetime(2026, 6, 21, 12, tzinfo=dt.UTC).timestamp()
    res = best_targets(eng, t)
    assert res["window"] is None and res["targets"] == []


def test_best_targets_content_and_consistency(engine: AstronomyEngine) -> None:
    res = best_targets(engine, NOON, min_alt=30.0, limit=200)
    win = res["window"]
    by_id = {t["id"]: t for t in res["targets"]}
    for tid in ("planet:jupiter", "planet:saturn", "dso:M31", "dso:M45", "moon"):
        assert tid in by_id, tid
    assert "sun" not in by_id
    scores = [t["score"] for t in res["targets"]]
    assert scores == sorted(scores, reverse=True)
    for t in res["targets"]:
        assert t["max_alt"] >= 30.0
        assert win["start"] <= t["above_from"] <= t["best_time"] <= t["above_until"] <= win["end"] + 1
        assert t["hours_above"] <= win["hours"] + 0.2
    # Cross-check the planner's fast matrix method against the full apparent position.
    m31 = by_id["dso:M31"]
    alt, _ = engine.altaz(engine.resolve("dso:M31"), m31["best_time"])
    assert abs(float(alt) - m31["max_alt"]) < 0.05
    assert abs(m31["max_alt"] - (90 - engine.observer.latitude + 41.27)) < 0.5  # culmination altitude
    # Showpieces outrank faint objects: planets and the Moon lead the default list.
    assert {t["kind"] for t in res["targets"][:4]} <= {"planet", "moon"}


def test_kind_filter(engine: AstronomyEngine) -> None:
    res = best_targets(engine, NOON, kind="planet")
    assert res["targets"] and {t["kind"] for t in res["targets"]} <= {"planet", "moon"}
    res = best_targets(engine, NOON, kind="dso", min_alt=60)
    assert res["targets"] and all(t["kind"] == "dso" and t["max_alt"] >= 60 for t in res["targets"])
