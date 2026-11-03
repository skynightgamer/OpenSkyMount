"""Independent cross-check of the astronomy engine against Astropy (ERFA/SOFA).

Skipped when astropy is not installed (``pip install astropy``).
"""

from __future__ import annotations

import datetime as dt

import pytest

from openskymount.astro.engine import AstronomyEngine
from openskymount.config import ObserverConfig
from openskymount.mount.geometry import separation

ARCSEC = 1.0 / 3600.0

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
            # Independent proper-motion propagation from the Hipparcos epoch (J1991.25).
            sc0 = SkyCoord(ra=target.ra_deg * u.deg, dec=target.dec_deg * u.deg,
                           pm_ra_cosdec=target.pm_ra * u.mas / u.yr, pm_dec=target.pm_dec * u.mas / u.yr,
                           distance=(1000.0 / max(target.parallax, 0.1)) * u.pc, radial_velocity=0 * u.km / u.s,
                           obstime=Time(target.epoch_jd, format="jd", scale="tt"), frame="icrs")
            sc = sc0.apply_space_motion(new_obstime=t).transform_to(frame)
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




@pytest.mark.parametrize("ra,dec", [(279.23, 38.78), (101.29, -16.72), (37.95, 89.26), (10.0, -60.0)])
def test_jnow_matches_astropy_tete(astropy_env, ra: float, dec: float) -> None:
    """ASCOM equTopocentric ("JNow"): our apparent place of date vs Astropy's TETE frame (topocentric)."""
    import warnings

    from astropy import units as u
    from astropy.coordinates import TETE, EarthLocation, SkyCoord
    from astropy.time import Time

    when = dt.datetime(2024, 1, 15, 22, 0, tzinfo=dt.UTC)
    obs = ObserverConfig(latitude=52.52, longitude=13.405, elevation_m=35.0)
    eng = AstronomyEngine(obs)
    loc = EarthLocation(lat=obs.latitude * u.deg, lon=obs.longitude * u.deg, height=obs.elevation_m * u.m)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ref = SkyCoord(ra=ra * u.deg, dec=dec * u.deg, frame="icrs").transform_to(
            TETE(obstime=Time(when), location=loc))
    got = eng.icrs_to_apparent(ra, dec, when.timestamp())
    assert separation(got[1], got[0], ref.dec.deg, ref.ra.deg) < 0.5 * ARCSEC
    back = eng.apparent_to_icrs(ref.ra.deg, ref.dec.deg, when.timestamp())
    assert separation(back[1], back[0], dec, ra) < 0.5 * ARCSEC
