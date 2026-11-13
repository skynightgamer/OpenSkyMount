"""Sky events calendar, checked against well-known events that do not depend on this code."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from openskymount.astro.engine import AstronomyEngine
from openskymount.astro.events import MAX_DAYS, upcoming_events
from openskymount.config import ObserverConfig
from openskymount.server.app import Observatory, create_app

from .conftest import T0


def ts(*ymd_hm: int) -> float:
    return dt.datetime(*ymd_hm, tzinfo=dt.UTC).timestamp()


def day(unix: float) -> dt.date:
    return dt.datetime.fromtimestamp(unix, dt.UTC).date()


def find(res: dict, kind: str, needle: str) -> list[dict]:
    return [e for e in res["events"] if e["kind"] == kind and needle in e["title"]]


@pytest.fixture(scope="module")
def first_half_2026(engine: AstronomyEngine) -> dict:
    return upcoming_events(engine, ts(2026, 1, 1), 180)


@pytest.fixture(scope="module")
def late_2026(engine: AstronomyEngine) -> dict:
    return upcoming_events(engine, ts(2026, 7, 1), 180)


def test_events_are_sorted_and_inside_the_window(first_half_2026: dict) -> None:
    times = [e["time"] for e in first_half_2026["events"]]
    assert times == sorted(times)
    assert all(first_half_2026["from"] <= t <= first_half_2026["to"] for t in times)
    ids = [e["id"] for e in first_half_2026["events"]]
    assert len(ids) == len(set(ids)), "an event was reported twice"


def test_window_is_capped(engine: AstronomyEngine) -> None:
    res = upcoming_events(engine, ts(2026, 1, 1), 10_000)
    assert res["days"] == MAX_DAYS


def test_moon_phases_match_published_dates(first_half_2026: dict) -> None:
    """Full moons of 2026 (UTC dates from the standard almanac), including the blue moon of 31 May."""
    full = [day(e["time"]) for e in find(first_half_2026, "moon_phase", "Full Moon")]
    for want in (dt.date(2026, 1, 3), dt.date(2026, 2, 1), dt.date(2026, 3, 3), dt.date(2026, 4, 2), dt.date(2026, 5, 1), dt.date(2026, 5, 31)):
        assert any(abs((d - want).days) <= 1 for d in full), want
    new = [day(e["time"]) for e in find(first_half_2026, "moon_phase", "New Moon")]
    assert any(abs((d - dt.date(2026, 2, 17)).days) <= 1 for d in new)  # the day of the annular eclipse


def test_seasons(first_half_2026: dict, late_2026: dict) -> None:
    spring = find(first_half_2026, "season", "March equinox")[0]
    assert abs((day(spring["time"]) - dt.date(2026, 3, 20)).days) <= 1
    assert abs((day(find(first_half_2026, "season", "June solstice")[0]["time"]) - dt.date(2026, 6, 21)).days) <= 1
    assert abs((day(find(late_2026, "season", "December solstice")[0]["time"]) - dt.date(2026, 12, 21)).days) <= 1
    assert "spring" in spring["detail"]


def test_southern_hemisphere_seasons_are_swapped() -> None:
    eng = AstronomyEngine(ObserverConfig(latitude=-33.9, longitude=151.2, name="Sydney"))
    res = upcoming_events(eng, ts(2026, 3, 1), 40)
    assert "autumn" in find(res, "season", "March equinox")[0]["detail"]


def test_jupiter_opposition_and_mercury_elongations(first_half_2026: dict) -> None:
    opp = find(first_half_2026, "opposition", "Jupiter")[0]
    assert abs((day(opp["time"]) - dt.date(2026, 1, 10)).days) <= 1
    assert opp["visible"], "the opposition happens by day (08:42 UTC) but Jupiter is up all night around it"
    east = find(first_half_2026, "elongation", "Mercury greatest eastern")[0]
    west = find(first_half_2026, "elongation", "Mercury greatest western")[0]
    assert abs((day(east["time"]) - dt.date(2026, 2, 19)).days) <= 1 and 17.5 < east["elongation_deg"] < 18.8
    assert abs((day(west["time"]) - dt.date(2026, 4, 3)).days) <= 1 and 27.0 < west["elongation_deg"] < 28.5


def test_conjunctions_have_a_small_separation_and_a_planet_target(first_half_2026: dict) -> None:
    conj = [e for e in first_half_2026["events"] if e["kind"] == "conjunction"]
    assert len(conj) > 20
    for e in conj:
        assert 0.0 <= e["separation_deg"] < 4.0
        assert e["target"].startswith("planet:")
    # Venus and Neptune were within 0.2 degrees on 7 March 2026 (a well-known close pair).
    pair = find(first_half_2026, "conjunction", "Venus near Neptune")[0]
    assert abs((day(pair["time"]) - dt.date(2026, 3, 7)).days) <= 1 and pair["separation_deg"] < 0.3
    assert pair["importance"] == 1  # Neptune needs optics: listed, not a highlight


def test_meteor_shower_peaks_follow_solar_longitude(first_half_2026: dict, late_2026: dict) -> None:
    quad = find(first_half_2026, "meteor_shower", "Quadrantids")[0]
    assert day(quad["time"]) in (dt.date(2026, 1, 3), dt.date(2026, 1, 4))
    assert quad["moon_illuminated"] > 0.9 and quad["importance"] == 2  # full Moon: not a highlight despite ZHR 80
    per = find(late_2026, "meteor_shower", "Perseids")[0]
    assert day(per["time"]) in (dt.date(2026, 8, 12), dt.date(2026, 8, 13))
    assert per["moon_illuminated"] < 0.1 and per["importance"] == 3  # new Moon on 12 August
    assert day(find(late_2026, "meteor_shower", "Geminids")[0]["time"]) == dt.date(2026, 12, 14)
    assert day(find(late_2026, "meteor_shower", "Leonids")[0]["time"]) in (dt.date(2026, 11, 17), dt.date(2026, 11, 18))
    assert per["target"].startswith("radec:")


def test_solar_eclipses_are_classified_like_the_published_ones(engine: AstronomyEngine, first_half_2026: dict, late_2026: dict) -> None:
    annular = find(first_half_2026, "eclipse", "solar")[0]
    assert annular["magnitude_class"] == "annular" and day(annular["time"]) == dt.date(2026, 2, 17)
    total = find(late_2026, "eclipse", "solar")[0]
    assert total["magnitude_class"] == "total" and day(total["time"]) == dt.date(2026, 8, 12)
    assert total["gamma"] == pytest.approx(0.8977, abs=0.01)  # published gamma of the 2026 eclipse
    # 2027-08-02 (gamma 0.142) and 2028-07-22 (gamma 0.606): both total.
    for start, d, gamma in ((ts(2027, 7, 20), dt.date(2027, 8, 2), 0.1421), (ts(2028, 7, 10), dt.date(2028, 7, 22), 0.6056)):
        e = find(upcoming_events(engine, start, 30), "eclipse", "solar")[0]
        assert e["magnitude_class"] == "total" and day(e["time"]) == d and e["gamma"] == pytest.approx(gamma, abs=0.01)


def test_what_the_observer_sees_of_the_august_2026_eclipse() -> None:
    """Reykjavik is in the path of totality, Madrid just outside it, Honolulu does not see it at all."""
    def solar(lat: float, lon: float) -> dict:
        eng = AstronomyEngine(ObserverConfig(latitude=lat, longitude=lon, elevation_m=10.0, name="x"))
        return find(upcoming_events(eng, ts(2026, 8, 5), 14), "eclipse", "solar")[0]

    reykjavik = solar(64.1466, -21.9426)
    assert reykjavik["visible"] and reykjavik["seen_fraction"] >= 1.0 and "From here: total" in reykjavik["detail"]
    assert abs(reykjavik["time"] - ts(2026, 8, 12, 17, 48)) < 120  # totality at about 17:48 UTC
    madrid = solar(40.4168, -3.7038)
    assert madrid["visible"] and 0.99 < madrid["seen_fraction"] < 1.0 and "partial" in madrid["detail"]
    assert not solar(21.3, -157.86)["visible"]


def test_lunar_eclipse_visibility_depends_on_the_site(first_half_2026: dict) -> None:
    """Total lunar eclipse of 3 March 2026 (greatest 11:33 UTC): Honolulu sees it at night, Berlin has the Moon set."""
    berlin = find(first_half_2026, "eclipse", "lunar")[0]
    assert berlin["title"] == "Total lunar eclipse" and abs(berlin["time"] - ts(2026, 3, 3, 11, 33)) < 600
    assert not berlin["visible"]
    honolulu = AstronomyEngine(ObserverConfig(latitude=21.3, longitude=-157.86, name="Honolulu"))
    seen = find(upcoming_events(honolulu, ts(2026, 2, 25), 14), "eclipse", "lunar")[0]
    assert seen["visible"] and seen["alt"] > 60.0


def test_events_endpoint(tmp_path) -> None:
    with TestClient(create_app(observatory=Observatory(state_dir=tmp_path))) as c:
        res = c.get("/api/events", params={"t": T0, "days": 60}).json()
        assert res["days"] == 60 and res["events"]
        assert all({"id", "kind", "time", "title", "detail", "importance"} <= set(e) for e in res["events"])
        top = c.get("/api/events", params={"t": T0, "days": 60, "min_importance": 3}).json()
        assert top["events"] and all(e["importance"] == 3 for e in top["events"])
        assert len(top["events"]) < len(res["events"])
        assert c.get("/api/events", params={"days": 500}).status_code == 422
