"""Satellites (TLE) and comets / asteroids (MPC elements): parsing, positions against independent references,
pass prediction and fast tracking in the simulator."""

from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pytest

from openskymount.astro.engine import AstronomyEngine
from openskymount.astro.orbits import (
    ElementError,
    OrbitStore,
    parse_mpc,
    parse_tle,
    tle_checksum,
    unpack_epoch,
)
from openskymount.config import AppConfig, ObserverConfig

from .conftest import Rig

# ISS element set from the Skyfield documentation (epoch 2014-01-20 22:23 UTC); checksums are valid.
ISS = """ISS (ZARYA)
1 25544U 98067A   14020.93268519  .00009878  00000-0  18200-3 0  5082
2 25544  51.6498 109.4756 0003572  55.9686 274.8005 15.49815350868473"""
L1, L2 = ISS.splitlines()[1:]
NO_REFRACTION = ObserverConfig(temperature_c=None, pressure_mbar=None)


def comet_line(ty: int, tm: int, td: float, q: float, e: float, peri: float, node: float, inc: float,
               g: float, k: float, name: str, number: str = "", otype: str = "P") -> str:
    """A line in the MPC CometEls.txt fixed-column format."""
    s = [" "] * 160

    def put(col: int, text: str) -> None:
        s[col:col + len(text)] = list(text)

    put(0, f"{number:>4}")
    put(4, otype)
    put(14, f"{ty:4d}")
    put(19, f"{tm:02d}")
    put(22, f"{td:7.4f}")
    put(30, f"{q:9.6f}")
    put(41, f"{e:8.6f}")
    put(51, f"{peri:8.4f}")
    put(61, f"{node:8.4f}")
    put(71, f"{inc:8.4f}")
    put(91, f"{g:4.1f}")
    put(96, f"{k:4.1f}")
    put(102, name)
    return "".join(s).rstrip()


def mpcorb_line(epoch: str, m: float, peri: float, node: float, inc: float, e: float, a: float, h: float,
                g: float, readable: str, packed: str = "00001") -> str:
    """A line in the MPCORB.DAT fixed-column format."""
    s = [" "] * 202

    def put(col: int, text: str) -> None:
        s[col:col + len(text)] = list(text)

    n = 0.9856076686 / a**1.5
    put(0, f"{packed:<7}")
    put(8, f"{h:5.2f}")
    put(14, f"{g:5.2f}")
    put(20, epoch)
    put(26, f"{m:9.5f}")
    put(37, f"{peri:9.5f}")
    put(48, f"{node:9.5f}")
    put(59, f"{inc:9.5f}")
    put(70, f"{e:9.7f}")
    put(80, f"{n:11.8f}")
    put(92, f"{a:11.7f}")
    put(166, readable)
    return "".join(s).rstrip()


# ------------------------------------------------------------------- independent references
def gmst_deg(jd_ut1: float) -> float:
    """Greenwich mean sidereal time, IAU 1982 (Vallado eq. 3-47)."""
    t = (jd_ut1 - 2451545.0) / 36525.0
    sec = 67310.54841 + (876600.0 * 3600 + 8640184.812866) * t + 0.093104 * t * t - 6.2e-6 * t**3
    return (sec % 86400.0) / 240.0


def sgp4_altaz(unix: float, obs: ObserverConfig) -> tuple[float, float]:
    """ISS alt/az straight from the sgp4 library: TEME -> pseudo-Earth-fixed with GMST, then local ENU on the WGS84
    ellipsoid. Independent of Skyfield's frame chain (ignores polar motion and UT1-UTC: a few arcseconds)."""
    from sgp4.api import Satrec

    jd = unix / 86400.0 + 2440587.5
    sat = Satrec.twoline2rv(L1, L2)
    err, r, _ = sat.sgp4(math.floor(jd - 0.5) + 0.5, jd - (math.floor(jd - 0.5) + 0.5))
    assert err == 0
    g = math.radians(gmst_deg(jd))
    x = math.cos(g) * r[0] + math.sin(g) * r[1]
    y = -math.sin(g) * r[0] + math.cos(g) * r[1]
    z = r[2]
    a, f = 6378.137, 1 / 298.257223563
    e2 = f * (2 - f)
    lat, lon, h = math.radians(obs.latitude), math.radians(obs.longitude), obs.elevation_m / 1000
    nn = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
    sx, sy, sz = ((nn + h) * math.cos(lat) * math.cos(lon), (nn + h) * math.cos(lat) * math.sin(lon),
                  (nn * (1 - e2) + h) * math.sin(lat))
    dx, dy, dz = x - sx, y - sy, z - sz
    east = -math.sin(lon) * dx + math.cos(lon) * dy
    north = -math.sin(lat) * math.cos(lon) * dx - math.sin(lat) * math.sin(lon) * dy + math.cos(lat) * dz
    up = math.cos(lat) * math.cos(lon) * dx + math.cos(lat) * math.sin(lon) * dy + math.sin(lat) * dz
    return math.degrees(math.atan2(up, math.hypot(east, north))), math.degrees(math.atan2(east, north)) % 360.0


K_GAUSS = 0.01720209895  # rad/day
OBLIQUITY_J2000 = math.radians(84381.448 / 3600.0)
C_AU_PER_DAY = 173.1446326846693


def kepler_helio(q: float, e: float, inc: float, node: float, peri: float, days_from_perihelion: float) -> np.ndarray:
    """Heliocentric equatorial J2000 position (AU) of an elliptic orbit: own Kepler-equation solution
    (Meeus ch. 30/33), rotated by ω, i, Ω and the J2000 obliquity."""
    a = q / (1 - e)
    n = K_GAUSS / a**1.5
    m = n * days_from_perihelion
    ecc_anom = m
    for _ in range(50):
        ecc_anom -= (ecc_anom - e * math.sin(ecc_anom) - m) / (1 - e * math.cos(ecc_anom))
    xv, yv = a * (math.cos(ecc_anom) - e), a * math.sqrt(1 - e * e) * math.sin(ecc_anom)
    w, o, i = math.radians(peri), math.radians(node), math.radians(inc)
    v = np.array([xv, yv, 0.0])

    def rz(t: float) -> np.ndarray:
        return np.array([[math.cos(t), -math.sin(t), 0], [math.sin(t), math.cos(t), 0], [0, 0, 1]])

    def rx(t: float) -> np.ndarray:
        return np.array([[1, 0, 0], [0, math.cos(t), -math.sin(t)], [0, math.sin(t), math.cos(t)]])

    return rx(OBLIQUITY_J2000) @ rz(o) @ rx(i) @ rz(w) @ v


def geocentric_radec(eng: AstronomyEngine, tt_jd: float, helio_at) -> tuple[float, float, float]:
    """Astrometric geocentric RA/Dec with light-time iteration. Only the Earth and Sun barycentric positions come
    from the DE421 ephemeris."""
    ts = eng.ts
    earth = eng.eph["earth"].at(ts.tt_jd(tt_jd)).position.au
    tau = 0.0
    for _ in range(5):
        t_emit = tt_jd - tau
        sun = eng.eph["sun"].at(ts.tt_jd(t_emit)).position.au
        d = sun + helio_at(t_emit) - earth
        tau = float(np.linalg.norm(d)) / C_AU_PER_DAY
    ra = math.degrees(math.atan2(d[1], d[0])) % 360.0
    dec = math.degrees(math.asin(d[2] / np.linalg.norm(d)))
    return ra, dec, float(np.linalg.norm(d))


def sep_arcsec(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    d = ((ra1 - ra2 + 180) % 360 - 180) * math.cos(math.radians(dec1))
    return math.hypot(d, dec1 - dec2) * 3600.0


# ------------------------------------------------------------------- parsing
def test_tle_parsing_and_checksums() -> None:
    sats = parse_tle(ISS)
    assert len(sats) == 1 and sats[0].norad == 25544 and sats[0].name == "ISS (ZARYA)"
    assert tle_checksum(L1) == 2 and tle_checksum(L2) == 3
    assert dt.datetime.fromtimestamp(sats[0].epoch_unix, dt.UTC).strftime("%Y-%m-%d %H:%M") == "2014-01-20 22:23"
    # Without a name line, and the 3LE "0 NAME" variant.
    assert parse_tle(f"{L1}\n{L2}")[0].name == "NORAD 25544"
    assert parse_tle(f"0 ISS\n{L1}\n{L2}")[0].name == "ISS"
    bad = L1[:68] + "9"
    with pytest.raises(ElementError, match="checksum"):
        parse_tle(f"X\n{bad}\n{L2}")
    with pytest.raises(ElementError):
        parse_tle("hello\nworld")


def test_mpc_parsing() -> None:
    assert unpack_epoch("K2555") == (2025, 5, 5.0)
    assert unpack_epoch("J9611") == (1996, 1, 1.0)
    c = comet_line(1986, 2, 5.4686, 0.586004, 0.967373, 111.8540, 59.0846, 162.1859, 11.1, 6.0, "1P/Halley",
                   number="0001")
    a = mpcorb_line("K2555", 188.70269, 73.27343, 80.25221, 10.58780, 0.0789126, 2.7660512, 3.34, 0.15,
                    "(1) Ceres")
    objs = parse_mpc(f"{c}\n\n{a}\n")
    assert [o.id for o in objs] == ["comet:1P/Halley", "asteroid:1"]
    assert objs[0].q == pytest.approx(0.586004) and objs[0].tp_day == pytest.approx(5.4686)
    assert objs[1].name == "(1) Ceres" and objs[1].q == pytest.approx(2.7660512 * (1 - 0.0789126))
    with pytest.raises(ElementError, match="line 1"):
        parse_mpc("garbage that is not an orbit")


def test_store_persists_and_resolves(tmp_path) -> None:
    path = tmp_path / "orbits.json"
    st = OrbitStore(path)
    st.add_text(ISS)
    st.add_text(comet_line(1990, 10, 28.54502, 0.330886, 0.850220, 186.23352, 334.75006, 11.94524, 11.5, 6.0,
                           "2P/Encke", number="0002"))
    again = OrbitStore(path)
    assert len(again) == 2 and "sat:25544" in again.satellites
    eng = AstronomyEngine(NO_REFRACTION, orbits=again)
    assert eng.resolve("ISS (ZARYA)").id == "sat:25544"
    assert eng.resolve("comet:2p/encke").kind == "comet"
    assert any(h["id"] == "sat:25544" for h in eng.search("iss", 5))
    again.remove("sat:25544")
    with pytest.raises(ValueError):
        eng.resolve("sat:25544")


# ------------------------------------------------------------------- positions
def test_satellite_altaz_matches_independent_sgp4_chain() -> None:
    st = OrbitStore()
    st.add_text(ISS)
    eng = AstronomyEngine(NO_REFRACTION, orbits=st)
    target = eng.resolve("sat:25544")
    start = dt.datetime(2014, 1, 21, tzinfo=dt.UTC).timestamp()
    passes = eng.satellite_passes(target, start, hours=24, min_alt=10)
    assert len(passes) >= 3
    for p in passes:
        assert p["rise"] < p["culminate"] < p["set"] and 10 < p["max_alt"] <= 90
        assert 3 * 60 < p["set"] - p["rise"] < 12 * 60  # ISS passes above 10° last a few minutes
        for u in np.linspace(p["rise"], p["set"], 9):
            alt, az = eng.altaz(target, float(u))
            ref_alt, ref_az = sgp4_altaz(float(u), NO_REFRACTION)
            assert sep_arcsec(float(az), float(alt), ref_az, ref_alt) < 30.0
        # Rise/set refer to 10° altitude.
        assert sgp4_altaz(p["rise"], NO_REFRACTION)[0] == pytest.approx(10.0, abs=0.05)
        assert sgp4_altaz(p["set"], NO_REFRACTION)[0] == pytest.approx(10.0, abs=0.05)


def test_comet_encke_matches_meeus_example_33a() -> None:
    """Meeus, Astronomical Algorithms (2nd ed.), example 33.a: comet Encke, 1990 Oct 6.0 TD, elements referred to
    J2000: geocentric astrometric RA 158.558965° (10h34m14.2s), Δ = 0.82427 AU. The full RA/Dec is also compared
    with an independent Kepler propagation written in this test."""
    a, e = 2.2091404, 0.8502196
    elements = dict(q=a * (1 - e), e=e, peri=186.23352, node=334.75006, inc=11.94524)
    line = comet_line(1990, 10, 28.54502, elements["q"], e, elements["peri"], elements["node"], elements["inc"],
                      11.5, 6.0, "2P/Encke", number="0002")
    st = OrbitStore()
    st.add_text(line)
    eng = AstronomyEngine(NO_REFRACTION, orbits=st)
    body = eng._skyfield_body(eng.resolve("comet:2P/Encke"))
    t = eng.ts.tt(1990, 10, 6.0)
    ra, dec, dist = eng.earth.at(t).observe(body).radec()
    ra_deg = float(ra.hours) * 15.0
    assert (ra_deg - 158.558965) * 3600 * math.cos(math.radians(19.16)) == pytest.approx(0.0, abs=0.5)
    assert float(dist.au) == pytest.approx(0.82427, abs=2e-5)
    # Independent two-body propagation (the CometEls line rounds the elements, so use them as parsed).
    el = st.minor["comet:2P/Encke"]
    tp = eng.ts.tt(1990, 10, el.tp_day).tt
    ref = geocentric_radec(eng, t.tt, lambda jd: kepler_helio(el.q, el.e, el.i, el.node, el.peri, jd - tp))
    assert sep_arcsec(ra_deg, float(dec.degrees), ref[0], ref[1]) < 1.0
    # Comet magnitude formula m = g + 5 log Δ + 2.5 k log r.
    unix = (t.tt - 2440587.5) * 86400.0 - 56.18  # TT -> UTC (ΔT ≈ 56.2 s in 1990)
    mag = eng.magnitude(eng.resolve("comet:2P/Encke"), unix)
    assert mag is not None and 5 < mag < 15


def test_asteroid_from_mpcorb_matches_independent_kepler() -> None:
    # Ceres-like elements at epoch 2025-05-05.0 TT; checked 300 days later.
    a, e, inc, node, peri, m0 = 2.7660512, 0.0789126, 10.58780, 80.25221, 73.27343, 188.70269
    st = OrbitStore()
    st.add_text(mpcorb_line("K2555", m0, peri, node, inc, e, a, 3.34, 0.15, "(1) Ceres"))
    eng = AstronomyEngine(NO_REFRACTION, orbits=st)
    target = eng.resolve("asteroid:1")
    assert target.name == "(1) Ceres" and target.kind == "asteroid"
    epoch = eng.ts.tt(2025, 5, 5.0).tt
    t = epoch + 300.0
    n = K_GAUSS / a**1.5
    days_since_peri = math.radians(m0) / n  # mean anomaly at epoch -> time since perihelion

    ref = geocentric_radec(eng, t, lambda jd: kepler_helio(a * (1 - e), e, inc, node, peri,
                                                            jd - epoch + days_since_peri))
    ra, dec, _ = eng.earth.at(eng.ts.tt_jd(t)).observe(eng._skyfield_body(target)).radec()
    assert sep_arcsec(float(ra.hours) * 15, float(dec.degrees), ref[0], ref[1]) < 1.0
    unix = (t - 2440587.5) * 86400.0 - 69.2
    mag = eng.magnitude(target, unix)
    assert mag is not None and 6.0 < mag < 9.5  # Ceres is always between about mag 6.6 and 9.3


# ------------------------------------------------------------------- fast tracking (stress test)
def test_tracking_an_iss_pass_in_the_simulator() -> None:
    """Satellite tracking stress test: GoTo a rising ISS, follow the 70° pass to the 10° limit. The cable-wrap
    plan must hold the whole pass (no unwind) and the true (ground-truth) error must stay small at ~1°/s."""
    rise = dt.datetime(2014, 1, 21, 3, 27, 40, tzinfo=dt.UTC).timestamp()
    cfg = AppConfig()
    cfg.control.min_target_altitude = 10.0
    rig = Rig(cfg, t0=rise)
    rig.engine.orbits.add_text(ISS)
    rig.run(1.0)
    assert rig.ctl.goto("sat:25544")["ok"]
    worst = 0.0
    tracked = 0.0
    max_rate = 0.0
    for _ in range(420):
        snap = rig.run(1.0, dt=0.05)
        if snap["mode"] == "tracking":
            tracked += 1.0
            worst = max(worst, snap["true_pointing_error"])
            max_rate = max(max_rate, abs(snap["position"]["az_rate"]))
        elif snap["mode"] == "idle":
            break
    msgs = [e["msg"] for e in rig.ctl.events]
    assert not any("unwind" in m for m in msgs), msgs
    assert any("went below the altitude limit" in m for m in msgs)
    assert tracked > 300  # caught within ~20 s of a ~6.5 min pass
    assert max_rate > 1.5  # a genuinely fast azimuth motion near culmination
    assert worst * 3600 < 30.0, f"worst true error {worst * 3600:.1f}″"
