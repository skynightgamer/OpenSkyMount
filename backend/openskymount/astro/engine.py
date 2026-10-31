"""Astronomy engine built on Skyfield and the JPL DE421 ephemeris.

Responsibilities:
* convert Unix time to Skyfield time (leap-second aware, offline)
* resolve target identifiers (stars, planets, Moon, Sun, Messier objects,
  arbitrary RA/Dec, fixed Alt/Az)
* compute apparent topocentric Alt/Az (optionally with refraction), vectorised over time
* provide the ICRS -> horizon rotation used by the browser sky map
* object information (RA/Dec, rise/transit/set, magnitudes, rates, field rotation)

Conventions: azimuth is measured from North through East, degrees in [0, 360).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import numpy as np
from skyfield import almanac
from skyfield.api import Star, load, load_file, wgs84
from skyfield.timelib import Time

from ..config import ObserverConfig
from .catalog import Catalog, load_catalog

SIDEREAL_RATE_DEG_S = 360.0 / 86164.0905  # Earth rotation, deg per SI second

PLANETS: dict[str, tuple[str, str]] = {
    # id: (display name, ephemeris key)
    "mercury": ("Mercury", "mercury"),
    "venus": ("Venus", "venus"),
    "mars": ("Mars", "mars"),
    "jupiter": ("Jupiter", "jupiter barycenter"),
    "saturn": ("Saturn", "saturn barycenter"),
    "uranus": ("Uranus", "uranus barycenter"),
    "neptune": ("Neptune", "neptune barycenter"),
}


@lru_cache(maxsize=1)
def _ephemeris() -> Any:
    import importlib.resources as resources

    path = resources.files("skyfield_data") / "data" / "de421.bsp"
    return load_file(str(path))


@lru_cache(maxsize=1)
def _timescale() -> Any:
    # builtin=True: use Skyfield's bundled leap-second and Delta T tables (no network).
    return load.timescale(builtin=True)


class TargetError(ValueError):
    pass


@dataclass(frozen=True)
class Target:
    id: str
    name: str
    kind: str  # star | planet | moon | sun | dso | radec | altaz
    ra_deg: float | None = None  # J2000 / ICRS for fixed objects
    dec_deg: float | None = None
    alt_deg: float | None = None  # only for kind == altaz
    az_deg: float | None = None
    magnitude: float | None = None
    constellation: str | None = None
    type_label: str = ""

    @property
    def solar_system(self) -> bool:
        return self.kind in ("planet", "moon", "sun")

    @property
    def is_sun(self) -> bool:
        return self.kind == "sun"

    @property
    def sidereal(self) -> bool:
        """Fixed on the celestial sphere (stars, DSOs, RA/Dec points)."""
        return self.kind in ("star", "dso", "radec")

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "type": self.type_label,
            "magnitude": self.magnitude,
            "constellation": self.constellation,
        }


class AstronomyEngine:
    def __init__(self, observer: ObserverConfig | None = None, catalog: Catalog | None = None) -> None:
        self.eph = _ephemeris()
        self.ts = _timescale()
        self.earth = self.eph["earth"]
        self.catalog = catalog or load_catalog()
        self.set_observer(observer or ObserverConfig())

    # ------------------------------------------------------------------ setup
    def set_observer(self, observer: ObserverConfig) -> None:
        observer.validate()
        self.observer = observer
        self.topos = wgs84.latlon(observer.latitude, observer.longitude, elevation_m=observer.elevation_m)
        self.site = self.earth + self.topos
        self._target_cache: dict[str, Any] = {}

    # ------------------------------------------------------------------- time
    def time(self, unix: float | np.ndarray) -> Time:
        """Unix timestamp(s) (UTC, POSIX: no leap seconds) -> Skyfield Time."""
        u = np.asarray(unix, dtype=float)
        days = np.floor(u / 86400.0)
        secs = u - days * 86400.0
        # POSIX days map 1:1 onto UTC calendar days; seconds within a day are UTC seconds.
        return self.ts.utc(1970, 1, 1 + days.astype(int), 0, 0, secs) if u.ndim else self.ts.utc(
            1970, 1, 1 + int(days), 0, 0, float(secs)
        )

    # ---------------------------------------------------------------- targets
    def resolve(self, target_id: str) -> Target:
        tid = target_id.strip()
        low = tid.lower()
        if low == "sun":
            return Target("sun", "Sun", "sun", magnitude=-26.7, type_label="Star (Sun)")
        if low == "moon":
            return Target("moon", "Moon", "moon", type_label="Natural satellite")
        if low.startswith("planet:") and low[7:] in PLANETS:
            return Target(low, PLANETS[low[7:]][0], "planet", type_label="Planet")
        if low in PLANETS:
            return Target("planet:" + low, PLANETS[low][0], "planet", type_label="Planet")
        if low.startswith("hip:"):
            try:
                star = self.catalog.star_by_hip(int(low[4:]))
            except (ValueError, KeyError) as exc:
                raise TargetError(f"unknown star {target_id!r}") from exc
            return Target(
                f"hip:{star['hip']}",
                self.catalog.star_label(star),
                "star",
                ra_deg=star["ra"],
                dec_deg=star["dec"],
                magnitude=star["mag"],
                constellation=star["con"] or None,
                type_label="Star",
            )
        if low.startswith("dso:"):
            dso = self.catalog.dso_by_id(tid[4:].upper())
            if dso is None:
                raise TargetError(f"unknown deep-sky object {target_id!r}")
            label = dso["id"] + (f" ({dso['name']})" if dso["name"] else "")
            return Target(
                "dso:" + dso["id"], label, "dso", ra_deg=dso["ra"], dec_deg=dso["dec"],
                magnitude=dso["mag"], type_label=dso["type"],
            )
        if low.startswith("radec:"):
            ra, dec = _parse_pair(tid[6:])
            if not (0 <= ra < 360 and -90 <= dec <= 90):
                raise TargetError("RA must be in [0,360) deg and Dec in [-90,90] deg")
            return Target(f"radec:{ra:.5f},{dec:.5f}", f"RA {ra:.3f}° Dec {dec:+.3f}°", "radec",
                          ra_deg=ra, dec_deg=dec, type_label="Sky position (J2000)")
        if low.startswith("altaz:"):
            alt, az = _parse_pair(tid[6:])
            if not -90 <= alt <= 90:
                raise TargetError("altitude must be in [-90, 90]")
            az %= 360.0
            return Target(f"altaz:{alt:.4f},{az:.4f}", f"Alt {alt:.2f}° Az {az:.2f}°", "altaz",
                          alt_deg=alt, az_deg=az, type_label="Fixed horizon position")
        # Fall back to a name search (exact match first).
        hits = self.catalog.search(tid, limit=1, planets=PLANETS)
        if hits and hits[0]["score"] >= 90:
            return self.resolve(hits[0]["id"])
        raise TargetError(f"unknown target {target_id!r}")

    def _skyfield_body(self, target: Target) -> Any:
        if target.kind == "planet":
            return self.eph[PLANETS[target.id.split(":", 1)[1]][1]]
        if target.kind == "moon":
            return self.eph["moon"]
        if target.kind == "sun":
            return self.eph["sun"]
        if target.ra_deg is not None and target.dec_deg is not None:
            body = self._target_cache.get(target.id)
            if body is None:
                body = Star(ra_hours=target.ra_deg / 15.0, dec_degrees=target.dec_deg)
                self._target_cache[target.id] = body
            return body
        raise TargetError(f"target {target.id} has no celestial position")

    def apparent(self, target: Target, t: Time) -> Any:
        return self.site.at(t).observe(self._skyfield_body(target)).apparent()

    # --------------------------------------------------------------- positions
    def _refraction_kwargs(self) -> dict[str, Any]:
        o = self.observer
        if o.temperature_c is None or o.pressure_mbar is None:
            return {}
        return {"temperature_C": o.temperature_c, "pressure_mbar": o.pressure_mbar}

    def altaz(self, target: Target, unix: float | np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Apparent (refracted) topocentric altitude/azimuth in degrees."""
        if target.kind == "altaz":
            shape = np.shape(unix)
            return np.full(shape, target.alt_deg, dtype=float), np.full(shape, target.az_deg, dtype=float)
        t = self.time(unix)
        alt, az, _ = self.apparent(target, t).altaz(**self._refraction_kwargs())
        return np.asarray(alt.degrees, dtype=float), np.asarray(az.degrees, dtype=float)

    def altaz_rates(self, target: Target, unix: float, dt: float = 1.0) -> tuple[float, float, float, float]:
        """(alt, az, alt_rate, az_rate) with rates from a central difference (deg/s)."""
        times = np.array([unix - dt, unix, unix + dt])
        alt, az = self.altaz(target, times)
        daz = (az[2] - az[0] + 540.0) % 360.0 - 180.0
        return float(alt[1]), float(az[1]), float((alt[2] - alt[0]) / (2 * dt)), float(daz / (2 * dt))

    def radec(self, target: Target, unix: float) -> dict[str, float]:
        """Astrometric J2000 (ICRS) and apparent-of-date equatorial coordinates."""
        if target.kind == "altaz":
            return self.altaz_to_radec(target.alt_deg or 0.0, target.az_deg or 0.0, unix)
        t = self.time(unix)
        astrometric = self.site.at(t).observe(self._skyfield_body(target))
        ra, dec, dist = astrometric.radec()
        ra_d, dec_d, _ = astrometric.apparent().radec(epoch="date")
        return {
            "ra_j2000": float(ra.hours) * 15.0,
            "dec_j2000": float(dec.degrees),
            "ra_date": float(ra_d.hours) * 15.0,
            "dec_date": float(dec_d.degrees),
            "distance_au": float(dist.au),
        }

    def altaz_to_radec(self, alt: float, az: float, unix: float, refracted: bool = True) -> dict[str, float]:
        """Sky coordinates of a horizon direction.

        ``refracted``: ``alt`` is an observed (refracted) altitude, as used for pointing; the
        configured refraction is removed before converting.
        """
        kw = self._refraction_kwargs()
        if refracted and kw and alt > -1.0:
            from skyfield.earthlib import refraction

            alt = alt - float(refraction(alt, kw["temperature_C"], kw["pressure_mbar"]))
        t = self.time(unix)
        pos = self.site.at(t).from_altaz(alt_degrees=alt, az_degrees=az)
        ra, dec, _ = pos.radec()
        ra_d, dec_d, _ = pos.radec(epoch=t)
        return {
            "ra_j2000": float(ra.hours) * 15.0,
            "dec_j2000": float(dec.degrees),
            "ra_date": float(ra_d.hours) * 15.0,
            "dec_date": float(dec_d.degrees),
            "distance_au": float("nan"),
        }

    def sun_altaz(self, unix: float | np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return self.altaz(self.resolve("sun"), unix)

    def horizon_matrix(self, unix: float) -> np.ndarray:
        """Rotation ICRS unit vector -> local (North, East, Up)."""
        return np.asarray(self.topos.rotation_at(self.time(unix)))

    def local_sidereal_time_deg(self, unix: float) -> float:
        t = self.time(unix)
        return float((t.gast * 15.0 + self.observer.longitude) % 360.0)

    # ------------------------------------------------------------ information
    def field_rotation_rate(self, alt: float, az: float) -> float:
        """Field rotation rate (deg/s) seen by an alt-az mount at (alt, az)."""
        ca = math.cos(math.radians(alt))
        if abs(ca) < 1e-6:
            return float("inf")
        return SIDEREAL_RATE_DEG_S * math.cos(math.radians(self.observer.latitude)) * math.cos(math.radians(az)) / ca

    def events(self, target: Target, unix: float, hours: float = 24.0) -> dict[str, Any]:
        """Next rise / transit / set within ``hours``. None if it does not occur."""
        if target.kind == "altaz":
            return {"rise": None, "transit": None, "set": None, "circumpolar": None}
        t0 = self.time(unix)
        t1 = self.time(unix + hours * 3600.0)
        body = self._skyfield_body(target)
        out: dict[str, Any] = {}
        try:
            for key, fn in (("rise", almanac.find_risings), ("set", almanac.find_settings)):
                times, flags = fn(self.site, body, t0, t1)
                real = [ti for ti, ok in zip(times, flags, strict=False) if ok]
                out[key] = _unix_of(real[0]) if real else None
            times = almanac.find_transits(self.site, body, t0, t1)
            out["transit"] = _unix_of(times[0]) if len(times) else None
        except Exception:  # pragma: no cover - defensive: almanac edge cases
            out.setdefault("rise", None)
            out.setdefault("set", None)
            out.setdefault("transit", None)
        if out["rise"] is None and out["set"] is None:
            alt, _ = self.altaz(target, unix)
            out["circumpolar"] = bool(alt > 0)
            out["never_rises"] = bool(alt <= 0)
        else:
            out["circumpolar"] = False
        return out

    def magnitude(self, target: Target, unix: float) -> float | None:
        if target.magnitude is not None:
            return target.magnitude
        if target.kind == "moon":
            return -12.7  # full-Moon value; phase-dependent brightness not modelled
        if target.kind == "planet":
            try:
                from skyfield.magnitudelib import planetary_magnitude

                t = self.time(unix)
                return float(planetary_magnitude(self.earth.at(t).observe(self._skyfield_body(target))))
            except Exception:
                return None
        return None

    def sun_separation(self, target: Target, unix: float) -> float:
        if target.is_sun:
            return 0.0
        t = self.time(unix)
        sun = self.site.at(t).observe(self.eph["sun"]).apparent()
        if target.kind == "altaz":
            pos = self.site.at(t).from_altaz(alt_degrees=target.alt_deg, az_degrees=target.az_deg)
            return float(sun.separation_from(pos).degrees)
        return float(sun.separation_from(self.apparent(target, t)).degrees)

    def moon_phase(self, unix: float) -> dict[str, float]:
        t = self.time(unix)
        phase = float(almanac.moon_phase(self.eph, t).degrees)
        illum = float(self.site.at(t).observe(self.eph["moon"]).apparent().fraction_illuminated(self.eph["sun"]))
        return {"phase_angle_deg": phase, "illuminated": illum}

    def info(self, target: Target, unix: float, with_events: bool = True) -> dict[str, Any]:
        alt, az, alt_rate, az_rate = self.altaz_rates(target, unix)
        rd = self.radec(target, unix)
        info: dict[str, Any] = {
            **target.summary(),
            "time": unix,
            **{k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in rd.items()},
            "alt": alt,
            "az": az,
            "alt_rate": alt_rate,
            "az_rate": az_rate,
            "above_horizon": alt > 0.0,
            "magnitude": self.magnitude(target, unix),
            "sun_separation": self.sun_separation(target, unix),
            "field_rotation_rate": self.field_rotation_rate(alt, az),
            "solar_system": target.solar_system,
        }
        if target.kind == "moon":
            info.update(self.moon_phase(unix))
        if with_events:
            info["events"] = self.events(target, unix)
        info["notes"] = self.tracking_notes(target, info)
        return info

    def tracking_notes(self, target: Target, info: dict[str, Any]) -> list[str]:
        notes = []
        if target.is_sun:
            notes.append("Solar target: automatic slewing is disabled for safety (see docs/safety.md).")
        if not info["above_horizon"]:
            notes.append("Below the horizon: GoTo is not possible right now.")
        if info["alt"] > 80.0:
            notes.append("Near the zenith: azimuth rate is high; an alt-az mount may lag (zenith keyhole).")
        if target.solar_system and not target.is_sun:
            notes.append("Solar-system object: position is recomputed from the ephemeris during tracking.")
        if target.kind == "altaz":
            notes.append("Fixed horizon position: the mount holds still, no sidereal tracking.")
        if not target.is_sun and info.get("sun_separation", 180.0) < 15.0:
            notes.append("Close to the Sun: GoTo is blocked by the solar exclusion zone.")
        return notes

    # -------------------------------------------------------------- sky frame
    def frame(self, unix: float) -> dict[str, Any]:
        """Everything the browser needs to render the sky at ``unix``."""
        t = self.time(unix)
        bodies = []
        site_at = self.site.at(t)
        refr = self._refraction_kwargs()
        for tid in ["sun", "moon", *("planet:" + p for p in PLANETS)]:
            target = self.resolve(tid)
            obs = site_at.observe(self._skyfield_body(target))
            ra, dec, dist = obs.radec()
            alt, az, _ = obs.apparent().altaz(**refr)
            bodies.append(
                {
                    **target.summary(),
                    "ra_j2000": float(ra.hours) * 15.0,
                    "dec_j2000": float(dec.degrees),
                    "alt": float(alt.degrees),
                    "az": float(az.degrees),
                    "magnitude": self.magnitude(target, unix),
                    "distance_au": float(dist.au),
                }
            )
        sun_alt = bodies[0]["alt"]
        return {
            "time": unix,
            "matrix": self.horizon_matrix(unix).tolist(),
            "lst_deg": self.local_sidereal_time_deg(unix),
            "bodies": bodies,
            "sun_alt": sun_alt,
            "sky_state": _sky_state(sun_alt),
            "moon": self.moon_phase(unix),
        }


def _sky_state(sun_alt: float) -> str:
    if sun_alt > -0.833:
        return "day"
    if sun_alt > -6:
        return "civil twilight"
    if sun_alt > -12:
        return "nautical twilight"
    if sun_alt > -18:
        return "astronomical twilight"
    return "night"


def _unix_of(t: Time) -> float:
    return float(t.utc_datetime().timestamp())


def _parse_pair(text: str) -> tuple[float, float]:
    parts = text.replace(" ", "").split(",")
    if len(parts) != 2:
        raise TargetError("expected two comma-separated numbers")
    try:
        a, b = float(parts[0]), float(parts[1])
    except ValueError as exc:
        raise TargetError("expected two comma-separated numbers") from exc
    if not (math.isfinite(a) and math.isfinite(b)):
        raise TargetError("coordinates must be finite")
    return a, b
