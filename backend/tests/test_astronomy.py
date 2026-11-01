"""Astronomy checks against independent references.

References:
* J. Meeus, "Astronomical Algorithms", 2nd ed. (1998), worked examples 13.b, 25.b, 33.a, 47.a.
* Astropy (ERFA/SOFA implementation, independent of Skyfield) where installed.
"""

from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pytest

from openskymount.astro.engine import AstronomyEngine, TargetError
from openskymount.config import ObserverConfig
from openskymount.mount.geometry import separation, vec_to_altaz

ARCSEC = 1.0 / 3600.0


def hms(h: float, m: float, s: float) -> float:
    return (h + m / 60 + s / 3600) * 15.0


def dms(d: float, m: float, s: float) -> float:
    sign = -1 if d < 0 or (d == 0 and m < 0) else 1
    return sign * (abs(d) + m / 60 + s / 3600)


def unix_from_tt(engine: AstronomyEngine, *args: float) -> float:
    return engine.ts.tt(*args).utc_datetime().timestamp()


def geocentric_apparent(engine: AstronomyEngine, body: str, unix: float) -> tuple[float, float]:
    t = engine.time(unix)
    ra, dec, _ = engine.earth.at(t).observe(engine.eph[body]).apparent().radec(epoch="date")
    return ra.hours * 15.0, dec.degrees


def test_unix_time_roundtrip(engine: AstronomyEngine) -> None:
    for stamp in (0.0, 1.0e9, 1_483_228_800.0 - 0.5, 1_793_482_518.354):
        t = engine.time(stamp)
        back = t.utc_datetime().timestamp()
        assert abs(back - stamp) < 1e-3
    arr = engine.time(np.array([1.0e9, 1.0e9 + 60.0]))
    assert abs((arr.tt[1] - arr.tt[0]) * 86400 - 60.0) < 1e-4


def test_leap_second_awareness(engine: AstronomyEngine) -> None:
    # TT - UTC was 69.184 s from 2017 onward (37 leap seconds + 32.184 s).
    t = engine.time(dt.datetime(2020, 1, 1, tzinfo=dt.UTC).timestamp())
    assert abs((t.tt - t.J * 0 - _utc_jd(t)) * 86400 - 69.184) < 1e-3


def _utc_jd(t) -> float:
    d = t.utc_datetime()
    return d.timestamp() / 86400.0 + 2440587.5


def test_meeus_25b_sun(engine: AstronomyEngine) -> None:
    unix = unix_from_tt(engine, 1992, 10, 13, 0, 0, 0)
    ra, dec = geocentric_apparent(engine, "sun", unix)
    assert abs(ra - hms(13, 13, 30.749)) * math.cos(math.radians(dec)) < 2 * ARCSEC
    assert abs(dec - dms(-7, 47, 1.74)) < 2 * ARCSEC


def test_meeus_33a_venus(engine: AstronomyEngine) -> None:
    unix = unix_from_tt(engine, 1992, 12, 20, 0, 0, 0)
    ra, dec = geocentric_apparent(engine, "venus", unix)
    assert abs(ra - hms(21, 4, 41.454)) * math.cos(math.radians(dec)) < 2 * ARCSEC
    assert abs(dec - dms(-18, 53, 16.84)) < 2 * ARCSEC


def test_meeus_47a_moon(engine: AstronomyEngine) -> None:
    unix = unix_from_tt(engine, 1992, 4, 12, 0, 0, 0)
    ra, dec = geocentric_apparent(engine, "moon", unix)
    # Meeus uses a truncated ELP-2000 theory (~10" accuracy).
    assert abs(ra - 134.688470) * math.cos(math.radians(dec)) < 15 * ARCSEC
    assert abs(dec - 13.768368) < 15 * ARCSEC


def test_meeus_13b_venus_horizontal() -> None:
    """Alt/Az of Venus from the US Naval Observatory, 1987-04-10 19:21:00 UT."""
    obs = ObserverConfig(
        latitude=dms(38, 55, 17), longitude=-dms(77, 3, 56), elevation_m=0.0,
        temperature_c=None, pressure_mbar=None,
    )
    eng = AstronomyEngine(obs)
    unix = dt.datetime(1987, 4, 10, 19, 21, 0, tzinfo=dt.UTC).timestamp()
    alt, az = eng.altaz(eng.resolve("venus"), unix)
    # Meeus: A = 68.0337° measured from South => 248.0337° from North; h = 15.1249°.
    # Tolerance covers Venus' topocentric parallax and the UT1 model (Meeus is geocentric).
    assert abs(float(alt) - 15.1249) < 0.01
    assert abs(float(az) - 248.0337) < 0.01


def test_polaris_altitude_close_to_latitude(engine: AstronomyEngine) -> None:
    for hour in range(0, 24, 3):
        unix = dt.datetime(2026, 1, 1, hour, tzinfo=dt.UTC).timestamp()
        alt, _ = engine.altaz(engine.resolve("Polaris"), unix)
        assert abs(float(alt) - engine.observer.latitude) < 1.0


def test_horizon_matrix_matches_full_computation(engine: AstronomyEngine) -> None:
    """The browser renders stars with the ICRS->horizon matrix; check it against Skyfield altaz."""
    from openskymount.config import ObserverConfig as OC

    eng = AstronomyEngine(OC(temperature_c=None, pressure_mbar=None))
    unix = dt.datetime(2026, 3, 1, 22, tzinfo=dt.UTC).timestamp()
    m = eng.horizon_matrix(unix)
    for name in ("Vega", "Sirius", "Capella", "Deneb", "Betelgeuse"):
        target = eng.resolve(name)
        star = eng.catalog.star_by_hip(int(target.id[4:]))
        year = 2000.0 + (unix - 946728000.0) / (365.25 * 86400)
        ra_d, dec_d = eng.catalog.position_at(star, year)  # what the browser receives
        ra, dec = math.radians(ra_d), math.radians(dec_d)
        v = np.array([math.cos(dec) * math.cos(ra), math.cos(dec) * math.sin(ra), math.sin(dec)])
        alt_m, az_m = vec_to_altaz(m @ v)
        alt, az = eng.altaz(target, unix)
        # Only annual aberration (<= 20.5") and light deflection are missing.
        assert separation(alt_m, az_m, float(alt), float(az)) < 25 * ARCSEC


def test_refraction_raises_low_objects() -> None:
    plain = AstronomyEngine(ObserverConfig(temperature_c=None, pressure_mbar=None))
    refr = AstronomyEngine(ObserverConfig(temperature_c=10.0, pressure_mbar=1010.0))
    unix = dt.datetime(2026, 11, 1, 1, tzinfo=dt.UTC).timestamp()
    # Find a star a few degrees above the horizon.
    for star in plain.catalog.stars[:200]:
        target = plain.resolve(f"hip:{star['hip']}")
        alt0, _ = plain.altaz(target, unix)
        if 2.0 < float(alt0) < 6.0:
            alt1, _ = refr.altaz(target, unix)
            assert 0.1 < float(alt1 - alt0) < 0.4  # ~ 10-20 arcmin near the horizon
            break
    else:  # pragma: no cover
        pytest.skip("no low star found")


def test_jupiter_direction_and_info(engine: AstronomyEngine) -> None:
    unix = dt.datetime(2026, 11, 1, 1, tzinfo=dt.UTC).timestamp()
    info = engine.info(engine.resolve("Jupiter"), unix)
    assert info["name"] == "Jupiter" and info["kind"] == "planet"
    assert info["above_horizon"] and 0 < info["alt"] < 90
    assert 0 <= info["az"] < 360
    assert info["magnitude"] is not None and -3.0 < info["magnitude"] < -1.5
    assert 3.9 < info["distance_au"] < 6.5
    ev = info["events"]
    assert ev["transit"] is not None and ev["transit"] > unix


def test_search_and_resolve(engine: AstronomyEngine) -> None:
    from openskymount.astro.engine import PLANETS

    assert engine.catalog.search("jupiter", planets=PLANETS)[0]["id"] == "planet:jupiter"
    assert engine.catalog.search("alpha lyr")[0]["id"] == "hip:91262"
    assert engine.catalog.search("andromeda galaxy")[0]["id"] == "dso:M31"
    assert engine.catalog.search("sir")[0]["name"].startswith("Sirius")
    assert engine.resolve("M42").id == "dso:M42"
    assert engine.resolve("hip:32349").name.startswith("Sirius")
    assert engine.resolve("radec:10.5,41.2").kind == "radec"
    assert engine.resolve("altaz:45,180").az_deg == 180.0
    for bad in ("hip:999999999", "radec:400,0", "radec:1", "altaz:95,0", "nonsense-object-xyz", "radec:nan,0"):
        with pytest.raises(TargetError):
            engine.resolve(bad)


def test_observer_validation() -> None:
    with pytest.raises(ValueError):
        AstronomyEngine(ObserverConfig(latitude=91.0))
    with pytest.raises(ValueError):
        AstronomyEngine(ObserverConfig(longitude=-181.0))


def test_southern_hemisphere_and_date_line() -> None:
    eng = AstronomyEngine(ObserverConfig(latitude=-33.87, longitude=151.21, name="Sydney"))
    unix = dt.datetime(2026, 6, 1, 12, tzinfo=dt.UTC).timestamp()  # 22:00 local
    alt, _ = eng.altaz(eng.resolve("dso:M7"), unix)
    assert float(alt) > 30  # Scorpius high in the June evening sky from Sydney
    alt_polaris, _ = eng.altaz(eng.resolve("Polaris"), unix)
    assert float(alt_polaris) < 0


def test_altaz_to_radec_inverts_apparent_position(engine: AstronomyEngine) -> None:
    unix = dt.datetime(2026, 11, 1, 1, tzinfo=dt.UTC).timestamp()
    for name in ("Capella", "jupiter", "Vega"):
        target = engine.resolve(name)
        alt, az = engine.altaz(target, unix)
        back = engine.altaz_to_radec(float(alt), float(az), unix)
        ref = engine.radec(target, unix)
        d_ra = (back["ra_j2000"] - ref["ra_j2000"]) * math.cos(math.radians(ref["dec_j2000"]))
        # Refraction removed; residual = aberration/deflection differences (< 25").
        assert math.hypot(d_ra, back["dec_j2000"] - ref["dec_j2000"]) < 25 * ARCSEC, name


def test_meeus_23a_proper_motion_apparent_place(engine: AstronomyEngine) -> None:
    """θ Persei, apparent place 2028 Nov 13.19 TD (includes proper motion, precession, nutation, aberration)."""
    from openskymount.astro.engine import Target

    ra0, dec0 = hms(2, 44, 11.986), dms(49, 13, 42.48)
    pm_ra_star = 0.03425 * 15 * 1000 * math.cos(math.radians(dec0))  # s/yr of RA -> mu_alpha* mas/yr
    target = Target("test:theta-per", "θ Per", "star", ra_deg=ra0, dec_deg=dec0,
                    pm_ra=pm_ra_star, pm_dec=-89.5, epoch_jd=2451545.0)
    t = engine.ts.tt(2028, 11, 13.19)
    ra, dec, _ = engine.earth.at(t).observe(engine._skyfield_body(target)).apparent().radec(epoch="date")
    exp_ra, exp_dec = hms(2, 46, 14.390), dms(49, 21, 7.45)
    assert abs(ra.hours * 15 - exp_ra) * math.cos(math.radians(exp_dec)) < 1.5 * ARCSEC
    assert abs(dec.degrees - exp_dec) < 1.5 * ARCSEC


def test_catalog_has_faint_stars_and_proper_motion(engine: AstronomyEngine) -> None:
    cat = engine.catalog
    assert len(cat.stars) > 40000 and max(s["mag"] for s in cat.stars) <= 8.0
    s61 = cat.star_by_hip(104214)  # 61 Cyg A: large, well-known proper motion and parallax
    assert abs(s61["pmra"] - 4168.3) < 5 and abs(s61["pmdec"] - 3269.2) < 5 and abs(s61["plx"] - 286.8) < 3
    # The browser's linear propagation agrees with Skyfield's rigorous astrometry.
    unix = dt.datetime(2026, 11, 1, tzinfo=dt.UTC).timestamp()
    year = 2000.0 + (unix - 946728000.0) / (365.25 * 86400)
    for hip in (104214, 69673, 57939, 32349):  # 61 Cyg A, Arcturus, Groombridge 1830, Sirius
        star = cat.star_by_hip(hip)
        ra_c, dec_c = cat.position_at(star, year)
        target = engine.resolve(f"hip:{hip}")
        ref = engine.earth.at(engine.time(unix)).observe(engine._skyfield_body(target)).radec()
        d_ra = (ra_c - ref[0].hours * 15) * math.cos(math.radians(dec_c))
        # Residual: parallax (<0.3") + aberration-free astrometric vs linear propagation.
        assert math.hypot(d_ra, dec_c - ref[1].degrees) < 1.0 * ARCSEC, hip
    # Proper motion matters: Arcturus moved > 60" since the Hipparcos epoch.
    arcturus = cat.star_by_hip(69673)
    ra91, dec91 = arcturus["ra"], arcturus["dec"]
    ra26, dec26 = cat.position_at(arcturus, 2026.8)
    assert math.hypot((ra26 - ra91) * math.cos(math.radians(dec91)), dec26 - dec91) > 60 * ARCSEC


def test_field_rotation_matches_parallactic_angle_derivative(engine: AstronomyEngine) -> None:
    """Closed-form alt-az field rotation rate vs numerical d(parallactic angle)/dt."""
    lat = math.radians(engine.observer.latitude)
    unix = dt.datetime(2026, 11, 1, 1, tzinfo=dt.UTC).timestamp()

    def parallactic(target, t: float) -> float:
        ha, dec, _ = engine.site.at(engine.time(t)).observe(engine._skyfield_body(target)).apparent().hadec()
        h, d = math.radians(ha.hours * 15), dec.radians
        return math.atan2(math.sin(h), math.tan(lat) * math.cos(d) - math.sin(d) * math.cos(h))

    checked = 0
    for name in ("Capella", "jupiter", "Vega", "Sirius", "M31", "Polaris"):
        target = engine.resolve(name)
        alt, az = (float(x) for x in engine.altaz(target, unix))
        if alt < 10 or alt > 80:
            continue
        dq = (parallactic(target, unix + 30) - parallactic(target, unix - 30) + math.pi) % (2 * math.pi) - math.pi
        numeric = math.degrees(dq) / 60.0
        formula = engine.field_rotation_rate(alt, az)
        # Convention: the reported rate is the field's rotation in the camera frame, which is
        # opposite to the change of the parallactic angle (q measured towards the west).
        assert abs(numeric + formula) < 0.02 * abs(formula) + 2e-6, (name, numeric, formula)
        checked += 1
    assert checked >= 3
