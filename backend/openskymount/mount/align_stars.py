"""Choosing stars for a pointing alignment.

The pointing model has two offsets (always fitted) and a base tilt (fitted from three points on). The tilt is only
measurable when the alignment stars are spread around the sky: stars clustered in one region make offset and tilt
indistinguishable. This picks bright, well-placed stars that are far apart and spread in azimuth, away from the
zenith (where the azimuth axis spins fast), the horizon (refraction) and the Sun, in a short order to visit.
"""

from __future__ import annotations

import itertools
import math
from typing import Any

import numpy as np

from .controller import MountController
from .geometry import separation

MIN_ALT = 25.0
ZENITH_MARGIN = 15.0  # stay this far below the mount's highest reachable altitude (and below 75 deg)
MAX_MAG = 2.8
POOL = {2: 24, 3: 22, 4: 18, 5: 15, 6: 13}  # brightest suitable stars tried, by group size (keeps the search small)
MIN_COUNT, MAX_COUNT = 2, 6
REUSE_SEP_DEG = 15.0  # a star this close (by name) to an existing alignment point is skipped


def _circular_max_gap(azimuths: list[float]) -> float:
    """Largest empty arc (deg) of the azimuth circle between neighbouring stars."""
    az = sorted(a % 360.0 for a in azimuths)
    gaps = [(az[(i + 1) % len(az)] - az[i]) % 360.0 for i in range(len(az))]
    return max(gaps) if len(az) > 1 else 360.0


def _score(group: list[dict[str, Any]]) -> float:
    """Higher is better: far apart, spread around the azimuth circle, bright."""
    seps = [separation(a["alt"], a["az"], b["alt"], b["az"]) for a, b in itertools.combinations(group, 2)]
    gap = _circular_max_gap([g["az"] for g in group])
    gap_penalty = max(0.0, gap - 360.0 / len(group) * 1.6) * 1.0  # one big empty arc leaves the tilt unconstrained
    return min(seps) + 0.1 * sum(seps) / len(seps) - gap_penalty - 6.0 * sum(g["magnitude"] for g in group) / len(group)


def suggest_alignment_stars(ctl: MountController, count: int = 3) -> dict[str, Any]:
    count = max(MIN_COUNT, min(MAX_COUNT, int(count)))
    eng = ctl.engine
    t = ctl.sim_time
    lim = ctl.limits
    ctl_cfg = ctl.config.control
    max_alt = min(75.0, lim.max_altitude - ZENITH_MARGIN)
    year = 2000.0 + (t - 946728000.0) / (365.25 * 86400.0)
    used = {p.label for p in ctl.model.points}
    s_alt, s_az = (float(x) for x in eng.sun_altaz(t))
    rot = eng.horizon_matrix(t)
    cands: list[dict[str, Any]] = []
    for star in eng.catalog.stars:
        if star["mag"] > MAX_MAG:
            break
        label = eng.catalog.star_label(star)
        if label in used or star["name"] in used:
            continue
        ra, dec = eng.catalog.position_at(star, year)
        r, d = math.radians(ra), math.radians(dec)
        v = rot @ np.array([math.cos(d) * math.cos(r), math.cos(d) * math.sin(r), math.sin(d)])
        alt = math.degrees(math.asin(max(-1.0, min(1.0, float(v[2])))))
        az = math.degrees(math.atan2(float(v[1]), float(v[0]))) % 360.0
        if not (MIN_ALT <= alt <= max_alt):
            continue
        if separation(alt, az, s_alt, s_az) < ctl_cfg.sun_exclusion_deg + 10.0:
            continue
        cands.append({"id": f"hip:{star['hip']}", "name": label, "magnitude": float(star["mag"]), "alt": alt, "az": az,
                      "constellation": star["con"] or None})
    cands = cands[: POOL[count]]  # catalog stars are sorted by magnitude
    out: dict[str, Any] = {"time": t, "requested": count, "stars": [], "message": None, "dark": s_alt < -6.0,
                           "sun_alt": s_alt}
    if len(cands) < count:
        out["message"] = (f"Only {len(cands)} suitable bright star(s) are between {MIN_ALT:.0f}° and {max_alt:.0f}° altitude "
                          f"and clear of the Sun right now; try later in the night." if cands else
                          "No suitable bright star is up right now (the Sun may be too high): try after dark.")
        group = cands
    else:
        group = max((list(g) for g in itertools.combinations(cands, count)), key=_score)
    if group:
        # Visit them in a short order from where the mount points now.
        here = ctl.pointing()
        order: list[dict[str, Any]] = []
        left = list(group)
        cur = here
        while left:
            nxt = min(left, key=lambda s: separation(cur[0], cur[1], s["alt"], s["az"]))
            order.append(nxt)
            left.remove(nxt)
            cur = (nxt["alt"], nxt["az"])
        out["stars"] = order
        if len(order) > 1:
            seps = [separation(a["alt"], a["az"], b["alt"], b["az"]) for a, b in itertools.combinations(order, 2)]
            out["min_separation_deg"] = min(seps)
            out["largest_azimuth_gap_deg"] = _circular_max_gap([s["az"] for s in order])
    return out
