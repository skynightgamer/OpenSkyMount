"""Observing planner: the darkness window and the best targets in it.

Candidates: planets, the Moon, the Messier objects and the brightest named stars.
Fixed objects are evaluated with one ICRS->horizon rotation per time step (accurate to
~25 arcsec, more than enough for planning); solar-system bodies use the full ephemeris.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from .engine import PLANETS, AstronomyEngine

STEP_S = 600.0  # 10-minute sampling of the night
SEARCH_S = 36 * 3600.0
BRIGHT_STAR_MAG = 1.6


def dark_window(engine: AstronomyEngine, unix: float, sun_limit: float = -12.0) -> tuple[float, float] | None:
    """Current or next interval with the Sun below ``sun_limit`` (start may be ``unix``), 1-minute resolution."""
    times = unix + np.arange(0.0, SEARCH_S + 1, 300.0)
    dark = engine.sun_altaz(times)[0] < sun_limit
    if not dark.any():
        return None
    i0 = int(np.argmax(dark))
    rest = ~dark[i0:]
    if not rest.any():
        return float(times[i0]), float(times[-1])  # polar night: dark for the whole search span
    i1 = i0 + int(np.argmax(rest))

    def refine(a: float, b: float, want_dark_at_b: bool) -> float:
        for _ in range(9):  # bisection down to ~1 min
            mid = 0.5 * (a + b)
            is_dark = float(engine.sun_altaz(mid)[0]) < sun_limit
            if is_dark == want_dark_at_b:
                b = mid
            else:
                a = mid
        return b

    start = float(times[i0]) if i0 == 0 else refine(float(times[i0 - 1]), float(times[i0]), True)
    end = refine(float(times[i1 - 1]), float(times[i1]), False)
    return start, end


def _candidates(engine: AstronomyEngine) -> list[dict[str, Any]]:
    cat = engine.catalog
    out: list[dict[str, Any]] = [
        {"id": "planet:" + p, "name": name, "kind": "planet", "type": "Planet"} for p, (name, _) in PLANETS.items()
    ]
    out.append({"id": "moon", "name": "Moon", "kind": "moon", "type": "Natural satellite"})
    for d in cat.dsos:
        out.append({"id": "dso:" + d["id"], "name": d["id"] + (f" {d['name']}" if d["name"] else ""),
                    "kind": "dso", "type": d["type"], "magnitude": d["mag"], "ra": d["ra"], "dec": d["dec"]})
    for s in cat.stars:
        if s["mag"] > BRIGHT_STAR_MAG:
            break
        if s["name"]:
            out.append({"id": f"hip:{s['hip']}", "name": s["name"], "kind": "star", "type": "Star",
                        "magnitude": s["mag"], "ra": s["ra"], "dec": s["dec"], "star": s})
    return out


def score(t: dict[str, Any], min_alt: float) -> float:
    """Rank for a beginner's list: visible for long, high up, and bright enough to see easily.

    Each term is 0..1 and weighted: observable time (capped at 6 h) x1.5, peak altitude above
    the limit x0.8, brightness (mag 10 -> 0, mag -2 or brighter -> 1) x2, plus a type weight:
    planets +0.8 and the Moon +0.5 (most rewarding in small optics), single stars -0.5 (a point
    of light; mainly useful for alignment), deep-sky objects 0.
    """
    time_term = min(t["hours_above"], 6.0) / 6.0
    alt_term = (t["max_alt"] - min_alt) / max(1.0, 90.0 - min_alt)
    mag = t["magnitude"] if t["magnitude"] is not None else 10.0
    bright_term = min(1.0, max(0.0, (10.0 - mag) / 12.0))
    type_term = {"planet": 0.8, "moon": 0.5, "star": -0.5}.get(t["kind"], 0.0)
    return round(1.5 * time_term + 0.8 * alt_term + 2.0 * bright_term + type_term, 4)


def best_targets(engine: AstronomyEngine, unix: float, min_alt: float = 30.0, sun_limit: float = -12.0,
                 kind: str = "all", limit: int = 30) -> dict[str, Any]:
    window = dark_window(engine, unix, sun_limit)
    if window is None:
        return {"window": None, "sun_limit": sun_limit, "min_alt": min_alt, "targets": [],
                "message": "The Sun does not get that low in the next 36 hours (polar summer / twilight)."}
    start, end = window
    n = max(2, int(math.ceil((end - start) / STEP_S)) + 1)
    times = np.linspace(start, end, n)
    step_h = (end - start) / (n - 1) / 3600.0

    cands = [c for c in _candidates(engine) if kind == "all" or c["kind"] == kind
             or (kind == "planet" and c["kind"] == "moon")]
    fixed = [c for c in cands if "ra" in c]
    moving = [c for c in cands if "ra" not in c]
    alts: dict[str, np.ndarray] = {}

    if fixed:
        year = 2000.0 + (0.5 * (start + end) - 946728000.0) / (365.25 * 86400.0)
        vecs = []
        for c in fixed:
            ra, dec = engine.catalog.position_at(c["star"], year) if "star" in c else (c["ra"], c["dec"])
            r, d = math.radians(ra), math.radians(dec)
            vecs.append((math.cos(d) * math.cos(r), math.cos(d) * math.sin(r), math.sin(d)))
        v = np.array(vecs).T  # 3 x N
        up = np.empty((n, len(fixed)))
        for k, tk in enumerate(times):
            up[k] = engine.horizon_matrix(float(tk))[2] @ v
        alt_fixed = np.degrees(np.arcsin(np.clip(up, -1, 1)))
        for j, c in enumerate(fixed):
            alts[c["id"]] = alt_fixed[:, j]
    for c in moving:
        alts[c["id"]] = engine.altaz(engine.resolve(c["id"]), times)[0]

    moon_alt = alts.get("moon")
    if moon_alt is None:
        moon_alt = engine.altaz(engine.resolve("moon"), times)[0]
    targets = []
    for c in cands:
        a = alts[c["id"]]
        above = a >= min_alt
        if not above.any():
            continue
        k = int(np.argmax(a))
        idx = np.nonzero(above)[0]
        mag = c.get("magnitude")
        if mag is None:
            mag = engine.magnitude(engine.resolve(c["id"]), float(times[k]))
        targets.append({
            "id": c["id"], "name": c["name"], "kind": c["kind"], "type": c["type"],
            "magnitude": mag,
            "max_alt": float(a[k]),
            "best_time": float(times[k]),
            "above_from": float(times[idx[0]]),
            "above_until": float(times[idx[-1]]),
            "hours_above": float(len(idx) * step_h),
        })
    for tgt in targets:
        tgt["score"] = score(tgt, min_alt)
    targets.sort(key=lambda tgt: -tgt["score"])
    mid = 0.5 * (start + end)
    return {
        "window": {"start": start, "end": end, "hours": (end - start) / 3600.0},
        "sun_limit": sun_limit,
        "min_alt": min_alt,
        "moon": {**engine.moon_phase(mid), "up_hours": float((moon_alt > 0).sum() * step_h)},
        "targets": targets[:limit],
        "total": len(targets),
    }
