"""Constellations as targets: names, stories, search, GoTo to a figure's centre."""

from __future__ import annotations

import math

import pytest
from fastapi.testclient import TestClient

from openskymount.astro.engine import AstronomyEngine, TargetError
from openskymount.astro.lore import LORE
from openskymount.config import AppConfig
from openskymount.mount.geometry import separation
from openskymount.server.app import Observatory, create_app

from .conftest import T0, Rig


def test_every_constellation_has_a_name_and_a_short_story(engine: AstronomyEngine) -> None:
    ids = {c["id"] for c in engine.catalog.constellations}
    assert ids == set(LORE), "catalog and lore disagree about the 88 constellations"
    for cid, (english, story) in LORE.items():
        assert english.strip(), cid
        assert 40 <= len(story) <= 320, f"{cid}: story length {len(story)}"
        assert story.endswith("."), cid
    # Every catalog entry (both halves of Serpens included) carries its English name for the sky map.
    assert all(c["english"] for c in engine.catalog.constellations)


def test_resolve_constellation_by_abbreviation(engine: AstronomyEngine) -> None:
    t = engine.resolve("con:Ori")
    assert (t.id, t.kind, t.constellation, t.type_label) == ("con:Ori", "constellation", "Ori", "Constellation")
    assert t.sidereal and not t.solar_system
    assert engine.resolve("con:ori").id == "con:Ori"  # case-insensitive
    with pytest.raises(TargetError):
        engine.resolve("con:Xyz")


def test_centres_lie_where_the_constellations_are(engine: AstronomyEngine) -> None:
    """Independent check: bright stars whose positions are well known (J2000, degrees)."""
    def sep(a: tuple[float, float], b: tuple[float, float]) -> float:
        ra1, d1, ra2, d2 = map(math.radians, (*a, *b))
        c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(ra1 - ra2)
        return math.degrees(math.acos(max(-1.0, min(1.0, c))))

    def centre(cid: str) -> tuple[float, float]:
        t = engine.resolve(f"con:{cid}")
        return t.ra_deg, t.dec_deg

    assert sep(centre("Ori"), (88.793, 7.407)) < 10.0  # Betelgeuse
    assert sep(centre("UMi"), (37.954, 89.264)) < 25.0  # Polaris
    assert sep(centre("Cru"), (186.650, -63.099)) < 8.0  # Acrux
    assert sep(centre("Sco"), (247.352, -26.432)) < 15.0  # Antares
    assert sep(centre("Lyr"), (279.235, 38.784)) < 12.0  # Vega


def test_search_finds_constellations_by_latin_and_english_name(engine: AstronomyEngine) -> None:
    assert engine.catalog.search("orion")[0]["id"] == "con:Ori"
    assert engine.catalog.search("great bear")[0]["id"] == "con:UMa"
    assert engine.catalog.search("greater dog")[0]["id"] == "con:CMa"
    hit = engine.catalog.search("canis major")[0]
    assert (hit["id"], hit["kind"]) == ("con:CMa", "constellation")
    # Star designations still win for star queries.
    assert engine.catalog.search("alpha lyrae")[0]["id"] == "hip:91262"


def test_info_carries_the_story_and_a_position(engine: AstronomyEngine) -> None:
    info = engine.info(engine.resolve("con:Ori"), T0)
    assert info["lore"]["english"] == "Orion"
    assert "hunter" in info["lore"]["story"].lower()
    assert math.isfinite(info["alt"]) and math.isfinite(info["ra_j2000"])


def test_goto_and_track_the_centre_of_a_figure() -> None:
    rig = Rig(AppConfig())
    target = rig.engine.resolve("con:Ori")
    assert float(rig.engine.altaz(target, T0)[0]) > 10.0  # Orion is up from Berlin at this time
    rig.ctl.goto("con:Ori", track=True)
    rig.run(150.0)
    assert rig.ctl.mode == "tracking"
    alt, az = rig.engine.altaz(target, rig.t)
    p_alt, p_az = rig.ctl.pointing()
    assert separation(p_alt, p_az, float(alt), float(az)) < 0.05


def test_object_endpoint_returns_the_lore(tmp_path) -> None:
    with TestClient(create_app(observatory=Observatory(state_dir=tmp_path))) as c:
        d = c.get("/api/object", params={"id": "con:Cyg", "t": T0}).json()
        assert d["kind"] == "constellation" and d["lore"]["english"] == "Swan"
        assert c.get("/api/object", params={"id": "con:Nope"}).status_code == 404
        hits = c.get("/api/search", params={"q": "swan"}).json()
        assert hits[0]["id"] == "con:Cyg"
        cat = c.get("/api/catalog").json()
        assert all("english" in con for con in cat["constellations"])
