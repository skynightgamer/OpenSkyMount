"""Sky events calendar: what is worth looking up for in the coming weeks, computed offline.

Moon phases, equinoxes and solstices, lunar and solar eclipses (with what the observer sees), Moon-planet and
planet-planet conjunctions, oppositions of the outer planets, greatest elongations of Mercury and Venus, and the main
meteor showers. Everything comes from the same DE421 ephemeris as the rest of the program (Skyfield almanac and
eclipse routines plus a little geometry); meteor shower peaks use the published solar longitudes of the maxima, so
their dates follow the year instead of a fixed calendar day.

Times are Unix seconds (UTC). Each event says whether it can be seen from the observer's site: the main object above
the horizon and the Sun low enough, evaluated at the event time.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from skyfield import almanac, eclipselib

from .engine import PLANETS, AstronomyEngine, _unix_of

MAX_DAYS = 180.0
CHUNK_DAYS = 30.0  # discrete searches run in chunks so that closely spaced state changes are not aliased away

MOON_PHASE_NAMES = ("New Moon", "First quarter Moon", "Full Moon", "Last quarter Moon")
SEASON_NAMES = ("March equinox", "June solstice", "September equinox", "December solstice")
ECLIPSE_LUNAR = ("Penumbral lunar eclipse", "Partial lunar eclipse", "Total lunar eclipse")

# Largest separations (degrees, geocentric) that are reported as conjunctions.
MOON_PLANET_MAX_SEP = 4.0
PLANET_PLANET_MAX_SEP = 3.0
ELONGATION_MIN_DEG = 10.0

SUN_RADIUS_KM = 695_700.0
MOON_RADIUS_KM = 1_737.4
EARTH_EQUATORIAL_RADIUS_KM = 6_378.137
GAMMA_CENTRAL = 0.9972  # the shadow axis hits the Earth's disc below this (Espenak)
GAMMA_PARTIAL = 1.5433  # a partial eclipse is visible somewhere on Earth below this
HYBRID_BAND = 0.003  # Moon/Sun apparent size ratio within 1 +- this: annular where it is far, total where it is near


@dataclass(frozen=True)
class Shower:
    name: str
    lam: float  # solar longitude of the maximum (degrees, of date), IMO calendar
    ra: float  # radiant, J2000 degrees
    dec: float
    zhr: int  # typical zenithal hourly rate at maximum


SHOWERS: tuple[Shower, ...] = (
    Shower("Quadrantids", 283.15, 230.0, 49.0, 80),
    Shower("Lyrids", 32.32, 271.0, 34.0, 18),
    Shower("Eta Aquariids", 45.5, 338.0, -1.0, 50),
    Shower("Southern Delta Aquariids", 125.0, 339.0, -16.0, 25),
    Shower("Perseids", 140.0, 48.0, 58.0, 100),
    Shower("Draconids", 195.4, 262.0, 54.0, 10),
    Shower("Orionids", 208.0, 95.0, 16.0, 20),
    Shower("Leonids", 235.27, 152.0, 22.0, 15),
    Shower("Geminids", 262.2, 112.0, 33.0, 150),
    Shower("Ursids", 270.7, 217.0, 76.0, 10),
)


def _unit(vec: np.ndarray) -> np.ndarray:
    """Unit vectors of a (3, N) array of positions."""
    return vec / np.linalg.norm(vec, axis=0)


def _separation_deg(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Angle (degrees) between (3, N) arrays of unit vectors, accurate for small angles."""
    cross = np.linalg.norm(np.cross(a, b, axis=0), axis=0)
    return np.degrees(np.arctan2(cross, np.sum(a * b, axis=0)))


def _parabolic_extremum(x: np.ndarray, y: np.ndarray, i: int) -> float:
    """Abscissa of the extremum of the parabola through points i-1, i, i+1."""
    y0, y1, y2 = y[i - 1], y[i], y[i + 1]
    den = y0 - 2.0 * y1 + y2
    if abs(den) < 1e-15:
        return float(x[i])
    off = 0.5 * (y0 - y2) / den
    return float(x[i] + off * (x[i + 1] - x[i - 1]) / 2.0)


class _Context:
    """Shared helpers: visibility at the observer's site and chunked discrete searches."""

    def __init__(self, engine: AstronomyEngine, t0: float, t1: float) -> None:
        self.engine = engine
        self.t0 = t0
        self.t1 = t1
        self.events: list[dict[str, Any]] = []

    def chunks(self) -> list[tuple[float, float]]:
        out = []
        a = self.t0
        while a < self.t1:
            b = min(self.t1, a + CHUNK_DAYS * 86400.0)
            out.append((a, b))
            a = b
        return out

    def visibility(self, target_id: str | None, unix: float) -> tuple[float | None, float]:
        """(altitude of the target or None, Sun altitude) at the observer's site."""
        sun_alt = float(self.engine.sun_altaz(unix)[0])
        if target_id is None:
            return None, sun_alt
        alt = float(self.engine.altaz(self.engine.resolve(target_id), unix)[0])
        return alt, sun_alt

    def observable_around(self, target_id: str, unix: float, half_window_h: float = 12.0) -> bool:
        """The target is above 5 deg while the sky is dark (Sun below -6 deg) at some time within +-``half_window_h``.

        For events that last days (oppositions, elongations) the exact moment says little: what matters is whether the
        object can be seen from here on the nights around it."""
        times = unix + np.arange(-half_window_h, half_window_h + 0.01, 0.5) * 3600.0
        alt = self.engine.altaz(self.engine.resolve(target_id), times)[0]
        sun = self.engine.sun_altaz(times)[0]
        return bool(np.any((alt > 5.0) & (sun < -6.0)))

    def add(self, kind: str, unix: float, title: str, detail: str, target: str | None, importance: int,
            visible: bool | None = None, alt: float | None = None, sun_alt: float | None = None, **extra: Any) -> None:
        if not (self.t0 <= unix <= self.t1):
            return
        if alt is None and sun_alt is None:
            alt, sun_alt = self.visibility(target, unix)
        self.events.append({
            "id": f"{kind}:{int(unix)}:{title}", "kind": kind, "time": unix, "title": title, "detail": detail,
            "target": target, "importance": importance, "alt": alt, "sun_alt": sun_alt,
            "visible": visible if visible is not None else _night_visible(alt, sun_alt), **extra,
        })


def _night_visible(alt: float | None, sun_alt: float | None) -> bool | None:
    """Above the horizon (5 deg) with the Sun below -6 deg (civil twilight over)."""
    if alt is None or sun_alt is None:
        return None
    return bool(alt > 5.0 and sun_alt < -6.0)


# ---------------------------------------------------------------------------- moon, seasons
def _moon_and_seasons(ctx: _Context) -> None:
    eng = ctx.engine
    for a, b in ctx.chunks():
        ta, tb = eng.time(a), eng.time(b)
        times, phases = almanac.find_discrete(ta, tb, almanac.moon_phases(eng.eph))
        for ti, ph in zip(times, phases, strict=True):
            u = _unix_of(ti)
            alt, sun_alt = ctx.visibility("moon", u)
            ctx.add("moon_phase", u, MOON_PHASE_NAMES[int(ph)],
                    "Moon above the horizon here" if alt is not None and alt > 0 else "Moon below the horizon here",
                    "moon" if int(ph) != 0 else None, 1, alt=alt, sun_alt=sun_alt, visible=False if int(ph) == 0 else _night_visible(alt, sun_alt),
                    phase=int(ph))
        times, seasons = almanac.find_discrete(ta, tb, almanac.seasons(eng.eph))
        for ti, s in zip(times, seasons, strict=True):
            ctx.add("season", _unix_of(ti), SEASON_NAMES[int(s)], "Start of astronomical " + (
                ("spring" if s == 0 else "summer" if s == 1 else "autumn" if s == 2 else "winter")
                if eng.observer.latitude >= 0 else
                ("autumn" if s == 0 else "winter" if s == 1 else "spring" if s == 2 else "summer")), None, 1,
                visible=None, alt=None, sun_alt=float(eng.sun_altaz(_unix_of(ti))[0]))


# ---------------------------------------------------------------------------- eclipses
def _lunar_eclipses(ctx: _Context) -> None:
    eng = ctx.engine
    t, kinds, _ = eclipselib.lunar_eclipses(eng.time(ctx.t0), eng.time(ctx.t1), eng.eph)
    for ti, y in zip(t, kinds, strict=True):
        u = _unix_of(ti)
        alt, sun_alt = ctx.visibility("moon", u)
        seen = alt is not None and alt > 0.0 and sun_alt < 0.0
        kind = int(y)
        where = f"Moon at {alt:.0f}° altitude here." if seen and alt is not None else "Not visible from here: the Moon is below the horizon."
        ctx.add("eclipse", u, ECLIPSE_LUNAR[kind], f"Greatest eclipse. {where}",
                "moon" if seen else None, 3 if kind == 2 else 2 if kind == 1 else 1, visible=seen, alt=alt, sun_alt=sun_alt,
                eclipse="lunar", magnitude_class=("penumbral", "partial", "total")[kind])


def _solar_eclipses(ctx: _Context) -> None:
    eng = ctx.engine
    sun, moon, earth = eng.eph["sun"], eng.eph["moon"], eng.eph["earth"]
    for a, b in ctx.chunks():
        times, phases = almanac.find_discrete(eng.time(a), eng.time(b), almanac.moon_phases(eng.eph))
        for ti, ph in zip(times, phases, strict=True):
            if int(ph) != 0:
                continue
            tn = _unix_of(ti)
            grid = tn + np.arange(-6 * 3600.0, 6 * 3600.0 + 1, 360.0)
            tt = eng.time(grid)
            e = earth.at(tt)
            m_obs, s_obs = e.observe(moon).apparent(), e.observe(sun).apparent()
            sep = _separation_deg(_unit(m_obs.position.au), _unit(s_obs.position.au))
            k = int(np.argmin(sep))
            d_moon = float(m_obs.distance().km[k])
            d_sun = float(s_obs.distance().km[k])
            gamma = math.radians(float(sep[k])) / (EARTH_EQUATORIAL_RADIUS_KM / d_moon)  # sin P is nearly P
            if gamma >= GAMMA_PARTIAL:
                continue
            r_moon = math.degrees(math.asin(MOON_RADIUS_KM / d_moon))
            r_sun = math.degrees(math.asin(SUN_RADIUS_KM / d_sun))
            ratio = r_moon / r_sun
            if gamma < GAMMA_CENTRAL:
                kind = "hybrid" if abs(ratio - 1.0) < HYBRID_BAND else "total" if ratio > 1.0 else "annular"
            else:
                kind = "partial"
            # What this observer sees: topocentric positions while the Sun is up, on a coarse grid and then, around the
            # best moment, every 10 s (the Moon moves 0.5° an hour, so 6 minutes would blur a near-total eclipse).
            def topocentric(times: np.ndarray) -> tuple[np.ndarray, ...]:
                s = eng.site.at(eng.time(times))
                mt, st = s.observe(moon).apparent(), s.observe(sun).apparent()
                sep_t = _separation_deg(_unit(mt.position.au), _unit(st.position.au))
                alt = eng.sun_altaz(times)[0]
                rm_t = np.degrees(np.arcsin(MOON_RADIUS_KM / mt.distance().km))
                rs_t = np.degrees(np.arcsin(SUN_RADIUS_KM / st.distance().km))
                cover = np.where(alt > 0.0, (rs_t + rm_t - sep_t) / (2.0 * rs_t), -1.0)  # magnitude seen from here
                return cover, sep_t, rs_t, rm_t, alt

            cover_up, sep_t, rs_t, rm_t, sun_alt = topocentric(grid)
            j = int(np.argmax(cover_up))
            fine = grid[j] + np.arange(-420.0, 421.0, 10.0)
            f_cover, f_sep, f_rs, f_rm, f_alt = topocentric(fine)
            if float(f_cover.max()) > float(cover_up[j]):
                cover_up, sep_t, rs_t, rm_t, sun_alt, grid, j = f_cover, f_sep, f_rs, f_rm, f_alt, fine, int(np.argmax(f_cover))
            here = float(cover_up[j]) > 0.0
            if here:
                central_here = sep_t[j] < abs(rs_t[j] - rm_t[j])
                how = (kind if kind in ("total", "annular") and central_here else "partial")
                pct = min(100.0, 100.0 * float(cover_up[j]))
                shown = f"{pct:.1f}" if pct >= 95.0 else f"{pct:.0f}"
                detail = (f"{kind.capitalize()} eclipse somewhere on Earth. From here: {how}, "
                          f"{shown} % of the Sun's diameter covered, "
                          f"Sun at {float(sun_alt[j]):.0f}° altitude.")
            else:
                detail = f"{kind.capitalize()} eclipse somewhere on Earth. Not visible from here."
            u = float(grid[j]) if here else tn
            ctx.add("eclipse", u, f"{kind.capitalize()} solar eclipse", detail, None, 3 if here else 2,
                    visible=here, alt=float(sun_alt[j]) if here else None,
                    sun_alt=float(sun_alt[j]) if here else float(eng.sun_altaz(tn)[0]),
                    eclipse="solar", magnitude_class=kind, gamma=round(gamma, 4), seen_fraction=max(0.0, float(cover_up[j])) if here else 0.0,
                    safety="Never look at the Sun without certified eclipse glasses; keep the mount away from it too.")


# ---------------------------------------------------------------------------- conjunctions, oppositions, elongations
def _conjunctions(ctx: _Context) -> None:
    eng = ctx.engine
    names = ["moon", *PLANETS]
    bodies = [eng.eph["moon"], *(eng.eph[PLANETS[p][1]] for p in PLANETS)]
    step = 3600.0
    grid = np.arange(ctx.t0 - step, ctx.t1 + 2 * step, step)
    e = eng.eph["earth"].at(eng.time(grid))
    units = [_unit(e.observe(b).apparent().position.au) for b in bodies]
    label = {"moon": "Moon", **{p: PLANETS[p][0] for p in PLANETS}}
    for i in range(len(bodies)):
        for j in range(i + 1, len(bodies)):
            limit = MOON_PLANET_MAX_SEP if i == 0 else PLANET_PLANET_MAX_SEP
            sep = _separation_deg(units[i], units[j])
            for k in range(1, len(grid) - 1):
                if sep[k] <= sep[k - 1] and sep[k] < sep[k + 1] and sep[k] < limit:
                    u = _parabolic_extremum(grid, sep, k)
                    a_id = names[i] if names[i] == "moon" else f"planet:{names[i]}"
                    b_id = f"planet:{names[j]}"
                    alt_a, sun_alt = ctx.visibility(a_id, u)
                    alt_b, _ = ctx.visibility(b_id, u)
                    alt = min(alt_a if alt_a is not None else 90.0, alt_b if alt_b is not None else 90.0)
                    sep_now = float(np.interp(u, grid, sep))
                    faint = bool({names[i], names[j]} & {"uranus", "neptune"})  # needs optics: listed, but not a highlight
                    ctx.add("conjunction", u, f"{label[names[i]]} near {label[names[j]]}",
                            f"Closest approach {sep_now:.1f}° (seen from the Earth's centre); "
                            + ("both above the horizon here." if alt > 0 else "not both above the horizon here."),
                            b_id, 1 if faint else 3 if sep_now < 1.0 else 2,
                            alt=alt, sun_alt=sun_alt, separation_deg=round(sep_now, 2))


def _outer_oppositions(ctx: _Context) -> None:
    eng = ctx.engine
    for pid in ("mars", "jupiter", "saturn", "uranus", "neptune"):
        body = eng.eph[PLANETS[pid][1]]
        f = almanac.oppositions_conjunctions(eng.eph, body)
        for a, b in ctx.chunks():
            times, kinds = almanac.find_discrete(eng.time(a), eng.time(b), f)
            for ti, kd in zip(times, kinds, strict=True):
                if int(kd) != 1:
                    continue
                u = _unix_of(ti)
                alt, sun_alt = ctx.visibility(f"planet:{pid}", u)
                ctx.add("opposition", u, f"{PLANETS[pid][0]} at opposition",
                        "Opposite the Sun: closest and brightest for the year, up all night.", f"planet:{pid}", 3 if pid in ("mars", "jupiter", "saturn") else 2,
                        alt=alt, sun_alt=sun_alt, visible=ctx.observable_around(f"planet:{pid}", u))


def _greatest_elongations(ctx: _Context) -> None:
    from skyfield.framelib import ecliptic_frame

    eng = ctx.engine
    step = 6 * 3600.0
    grid = np.arange(ctx.t0 - 2 * step, ctx.t1 + 3 * step, step)
    e = eng.eph["earth"].at(eng.time(grid))
    sun = e.observe(eng.eph["sun"]).apparent()
    sun_lon = sun.frame_latlon(ecliptic_frame)[1].degrees
    su = _unit(sun.position.au)
    for pid in ("mercury", "venus"):
        p = e.observe(eng.eph[PLANETS[pid][1]]).apparent()
        sep = _separation_deg(_unit(p.position.au), su)
        lon = p.frame_latlon(ecliptic_frame)[1].degrees
        for k in range(1, len(grid) - 1):
            if sep[k] >= sep[k - 1] and sep[k] > sep[k + 1] and sep[k] > ELONGATION_MIN_DEG:
                u = _parabolic_extremum(grid, sep, k)
                peak = float(sep[k])
                east = ((float(lon[k]) - float(sun_lon[k]) + 180.0) % 360.0) - 180.0 > 0.0
                alt, sun_alt = ctx.visibility(f"planet:{pid}", u)
                ctx.add("elongation", u, f"{PLANETS[pid][0]} greatest {'eastern' if east else 'western'} elongation",
                        f"{peak:.0f}° from the Sun: best placement in the {'evening' if east else 'morning'} sky.",
                        f"planet:{pid}", 2, alt=alt, sun_alt=sun_alt, elongation_deg=round(peak, 1),
                        visible=ctx.observable_around(f"planet:{pid}", u))


# ---------------------------------------------------------------------------- meteor showers
def _solar_longitudes(eng: AstronomyEngine, grid: np.ndarray) -> np.ndarray:
    t = eng.time(grid)
    sun = eng.eph["earth"].at(t).observe(eng.eph["sun"]).apparent()
    return np.asarray(sun.ecliptic_latlon(epoch="date")[1].degrees, dtype=float)


def _meteor_showers(ctx: _Context) -> None:
    eng = ctx.engine
    step = 6 * 3600.0
    grid = np.arange(ctx.t0 - step, ctx.t1 + 2 * step, step)
    lon = _solar_longitudes(eng, grid)
    for sh in SHOWERS:
        d = ((lon - sh.lam + 180.0) % 360.0) - 180.0
        for k in range(len(grid) - 1):
            if d[k] < 0.0 <= d[k + 1] and d[k + 1] - d[k] < 5.0:
                u = float(grid[k] + (grid[k + 1] - grid[k]) * (-d[k]) / (d[k + 1] - d[k]))
                moon = eng.moon_phase(u)["illuminated"]
                ra_alt = float(eng.altaz(eng.resolve(f"radec:{sh.ra:.4f},{sh.dec:.4f}"), u)[0])
                sun_alt = float(eng.sun_altaz(u)[0])
                dark_moon = moon < 0.3
                ctx.add("meteor_shower", u, f"{sh.name} peak",
                        f"About {sh.zhr} meteors/hour under a dark sky at the maximum. Moon {moon * 100:.0f} % lit: "
                        + ("a dark sky." if dark_moon else "moonlight will hide the fainter ones."),
                        f"radec:{sh.ra:.4f},{sh.dec:.4f}", 3 if sh.zhr >= 80 and dark_moon else 2,
                        alt=ra_alt, sun_alt=sun_alt, visible=bool(ra_alt > 10.0 and sun_alt < -6.0),
                        zhr=sh.zhr, moon_illuminated=round(moon, 3), radiant={"ra": sh.ra, "dec": sh.dec})


# ---------------------------------------------------------------------------- entry point
def upcoming_events(engine: AstronomyEngine, unix: float, days: float = 45.0) -> dict[str, Any]:
    """Events between ``unix`` and ``unix + days`` (at most MAX_DAYS), sorted by time."""
    days = max(1.0, min(MAX_DAYS, float(days)))
    ctx = _Context(engine, unix, unix + days * 86400.0)
    _moon_and_seasons(ctx)
    _lunar_eclipses(ctx)
    _solar_eclipses(ctx)
    _conjunctions(ctx)
    _outer_oppositions(ctx)
    _greatest_elongations(ctx)
    _meteor_showers(ctx)
    ctx.events.sort(key=lambda ev: (ev["time"], -ev["importance"]))
    return {
        "from": unix, "to": ctx.t1, "days": days, "site": engine.observer.name,
        "events": ctx.events,
    }
