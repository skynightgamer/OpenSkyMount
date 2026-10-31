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
        ra, dec = math.radians(target.ra_deg), math.radians(target.dec_deg)
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


# --------------------------------------------------------------- astropy
astropy = pytest.importorskip("astropy", reason="astropy cross-check not installed")


@pytest.fixture(scope="module")
def astropy_env():
    from astropy.utils import iers

    iers.conf.auto_download = False
    iers.conf.iers_degraded_accuracy = "ignore"
    return iers


@pytest.mark.parametrize(
    "lat,lon,when",
    [
        (52.52, 13.405, dt.datetime(2021, 8, 15, 21, 30, tzinfo=dt.UTC)),
        (-33.87, 151.21, dt.datetime(2022, 3, 1, 11, 0, tzinfo=dt.UTC)),
        (19.82, -155.47, dt.datetime(2020, 12, 21, 8, 0, tzinfo=dt.UTC)),
    ],
)
def test_crosscheck_astropy(astropy_env, lat: float, lon: float, when: dt.datetime) -> None:
    import warnings

    from astropy import units as u
    from astropy.coordinates import AltAz, EarthLocation, SkyCoord, get_body, solar_system_ephemeris
    from astropy.time import Time

    eng = AstronomyEngine(ObserverConfig(latitude=lat, longitude=lon, elevation_m=0.0,
                                         temperature_c=None, pressure_mbar=None))
    unix = when.timestamp()
    loc = EarthLocation(lat=lat * u.deg, lon=lon * u.deg, height=0 * u.m)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        t = Time(when)
        frame = AltAz(obstime=t, location=loc, pressure=0 * u.hPa)
        checked = 0
        for name in ("Vega", "Sirius", "Arcturus", "Canopus", "Polaris", "Altair"):
            target = eng.resolve(name)
            sc = SkyCoord(ra=target.ra_deg * u.deg, dec=target.dec_deg * u.deg, frame="icrs").transform_to(frame)
            alt, az = eng.altaz(target, unix)
            assert separation(float(alt), float(az), sc.alt.deg, sc.az.deg) < 5 * ARCSEC, name
            checked += 1
        with solar_system_ephemeris.set("builtin"):
            for planet in ("jupiter", "saturn", "mars"):
                body = get_body(planet, t, loc).transform_to(frame)
                alt, az = eng.altaz(eng.resolve(planet), unix)
                # Astropy's builtin ephemeris (ERFA plan94) is good to tens of arcseconds.
                assert separation(float(alt), float(az), body.alt.deg, body.az.deg) < 60 * ARCSEC, planet
                checked += 1
    assert checked == 9
