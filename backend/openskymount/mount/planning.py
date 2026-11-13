"""Planning helpers for a phone (or camera) on the mount: mosaics of a sky region and long holds on one field.

* ``mosaic_plan`` lays out a grid of overlapping frames around a centre (a panorama of the Milky Way, a wide field
  of a constellation), checks every frame against the mount's limits and the Sun, and returns observing-session items.
* ``timelapse_plan`` describes holding one target for a while: frames, how far the field rotates (an alt-az mount
  cannot avoid it), where the target goes, when it sets, and when tracking would reach the cable-wrap limit.

Nothing here moves the mount. Times are estimates from the configured axis limits, not measured on hardware.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ..astro.engine import Target
from .controller import CommandError, MountController
from .geometry import separation, wrap180
from .preview import _tracking_time_to_cable_limit, axis_time

MAX_PANELS = 64
SETTLE_S = 5.0  # per panel: slew settling and a pause before the frames start
MIN_OVERLAP, MAX_OVERLAP = 0.0, 0.6
MIN_FOV, MAX_FOV = 0.2, 150.0
MAX_TIMELAPSE_MIN = 12 * 60


def _frame(f: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """East and north unit vectors (equatorial) at direction ``f``: RA increases toward east."""
    z = np.array([0.0, 0.0, 1.0])
    e = np.cross(z, f)
    norm = np.linalg.norm(e)
    e = np.array([0.0, 1.0, 0.0]) if norm < 1e-9 else e / norm
    return e, np.cross(f, e)


def _radec(v: np.ndarray) -> tuple[float, float]:
    v = v / np.linalg.norm(v)
    return math.degrees(math.atan2(v[1], v[0])) % 360.0, math.degrees(math.asin(max(-1.0, min(1.0, float(v[2])))))


def mosaic_grid(ra: float, dec: float, cols: int, rows: int, fov_w: float, fov_h: float, overlap: float) -> list[dict[str, Any]]:
    """Frame centres of a cols x rows mosaic about (ra, dec) (J2000 degrees), in visiting order.

    Frames sit on a longitude/latitude grid in the equatorial frame of the centre. Rows away from the centre line are
    spread wider in longitude (by 1/cos of their latitude) so neighbours still overlap by the requested fraction. The
    order is a boustrophedon from the north row down, starting at the west end (frames in the west set first).
    """
    c = math.radians(ra), math.radians(dec)
    f = np.array([math.cos(c[1]) * math.cos(c[0]), math.cos(c[1]) * math.sin(c[0]), math.sin(c[1])])
    e, n = _frame(f)
    step_x = fov_w * (1.0 - overlap)
    step_y = fov_h * (1.0 - overlap)
    panels: list[dict[str, Any]] = []
    for j in range(rows):
        y = ((rows - 1) / 2.0 - j) * step_y  # first row is the northern one
        spread = 1.0 / max(0.2, math.cos(math.radians(y)))
        row = [(i, (i - (cols - 1) / 2.0) * step_x * spread) for i in range(cols)]  # (column, longitude), west to east
        if j % 2 == 1:
            row.reverse()
        for col, x in row:
            xr, yr = math.radians(x), math.radians(y)
            v = math.cos(yr) * (math.cos(xr) * f + math.sin(xr) * e) + math.sin(yr) * n
            p_ra, p_dec = _radec(v)
            panels.append({"index": len(panels), "row": j, "col": col, "ra": p_ra, "dec": p_dec, "x": x, "y": y})
    return panels


def mosaic_plan(ctl: MountController, target_id: str, t: float, cols: int, rows: int, overlap: float, fov_w: float,
                fov_h: float, dwell_s: float) -> dict[str, Any]:
    """A mosaic centred on a target: the frames, whether each can be reached, the time it takes, session items."""
    if not (1 <= cols <= 16 and 1 <= rows <= 16) or cols * rows > MAX_PANELS:
        raise CommandError(f"a mosaic has 1 to {MAX_PANELS} frames (at most 16 per side)")
    if not (MIN_OVERLAP <= overlap <= MAX_OVERLAP):
        raise CommandError("overlap must be between 0 and 60 %")
    if not (MIN_FOV <= fov_w <= MAX_FOV and MIN_FOV <= fov_h <= MAX_FOV):
        raise CommandError(f"field of view must be between {MIN_FOV} and {MAX_FOV} degrees")
    if not (0.0 <= dwell_s <= 3600.0):
        raise CommandError("time per frame must be between 0 and 3600 s")
    eng = ctl.engine
    try:
        centre = eng.resolve(target_id)
    except Exception as exc:  # TargetError
        raise CommandError(str(exc)) from exc
    if centre.kind == "altaz":
        raise CommandError("a mosaic needs a sky position or object, not a fixed horizon position")
    rd = eng.radec(centre, t)
    panels = mosaic_grid(rd["ra_j2000"], rd["dec_j2000"], cols, rows, fov_w, fov_h, overlap)
    lim = ctl.limits
    ctl_cfg = ctl.config.control
    prev = ctl.pointing()
    total = 0.0
    problems = 0
    for p in panels:
        tid = f"radec:{p['ra']:.5f},{p['dec']:.5f}"
        target = eng.resolve(tid)
        alt, az, _, _ = eng.altaz_rates(target, t)
        reasons = []
        if alt < ctl_cfg.min_target_altitude:
            reasons.append("below the altitude limit")
        if alt > lim.max_altitude:
            reasons.append("above the payload's reachable altitude")
        if not ctl._solar_policy_allows() and eng.sun_separation(target, t) < ctl_cfg.sun_exclusion_deg:
            reasons.append("too close to the Sun")
        p.update({"target": tid, "alt": alt, "az": az, "reachable": not reasons, "problems": reasons})
        problems += bool(reasons)
        # Slew time from the previous frame (both axes at once), approximated on the alt/az axes.
        d_alt = alt - prev[0]
        d_az = wrap180(az - prev[1])
        total += max(axis_time(d_alt, lim.max_rate, lim.max_accel), axis_time(d_az, lim.max_rate, lim.max_accel)) + SETTLE_S
        prev = (alt, az)
    total += dwell_s * len(panels)
    step_x, step_y = fov_w * (1.0 - overlap), fov_h * (1.0 - overlap)
    return {
        "centre": {"id": centre.id, "name": centre.name, "ra": rd["ra_j2000"], "dec": rd["dec_j2000"]},
        "time": t, "cols": cols, "rows": rows, "overlap": overlap, "fov": {"w": fov_w, "h": fov_h},
        "panels": panels,
        "coverage": {"w": (cols - 1) * step_x + fov_w, "h": (rows - 1) * step_y + fov_h},
        "total_s": total, "unreachable": problems,
        "estimate": "Slew times from the configured axis limits plus settling; not measured on hardware. Frames are drawn "
                    "as the camera would see them now; on an alt-az mount the field rotates as the sky turns.",
        "items": [{"target": p["target"], "duration_s": dwell_s, "label": f"{centre.name}: frame {p['index'] + 1}/{len(panels)}"}
                  for p in panels if p["reachable"]],
    }


def timelapse_plan(ctl: MountController, target_id: str, t: float, minutes: float, interval_s: float) -> dict[str, Any]:
    """Holding ``target_id`` for ``minutes`` from ``t`` with one frame every ``interval_s`` seconds."""
    if not (1.0 <= minutes <= MAX_TIMELAPSE_MIN):
        raise CommandError(f"duration must be between 1 and {MAX_TIMELAPSE_MIN} minutes")
    if not (0.5 <= interval_s <= 3600.0):
        raise CommandError("interval must be between 0.5 and 3600 s")
    if interval_s > minutes * 60.0:
        raise CommandError("the interval is longer than the whole run")
    eng = ctl.engine
    try:
        target: Target = eng.resolve(target_id)
    except Exception as exc:
        raise CommandError(str(exc)) from exc
    duration = minutes * 60.0
    times = t + np.arange(0.0, duration + 1e-6, 30.0)
    alt, az = eng.altaz(target, times)
    sun_alt = eng.sun_altaz(times)[0]
    min_alt = ctl.config.control.min_target_altitude
    below = np.nonzero(alt < min_alt)[0] if target.kind != "altaz" else np.array([], dtype=int)
    sets_in = float(times[below[0]] - t) if len(below) else None
    # Field rotation of an alt-az mount: the change of the parallactic angle, from the instantaneous rate.
    rate = np.array([eng.field_rotation_rate(float(a), float(z)) for a, z in zip(alt, az, strict=True)])
    rate = np.where(np.isfinite(rate), rate, 0.0)
    held = len(below) == 0
    stop = int(below[0]) if len(below) else len(times)
    rotation = float(np.trapezoid(rate[:stop], times[:stop])) if stop > 1 else 0.0
    unwrapped_az = np.degrees(np.unwrap(np.radians(az[:max(stop, 1)])))
    cable_s: float | None = None
    az_axis_start: float | None = None
    if target.kind != "altaz":
        cur_axis = ctl.status_cache.az.position
        _, m_az = ctl.model.sky_to_mount(float(alt[0]), float(az[0]))
        picked = ctl._choose_az(target, m_az, cur_axis)
        if picked is not None:
            az_axis_start = picked
            cable_s = _tracking_time_to_cable_limit(ctl, target, t, picked)
            if cable_s is not None and cable_s > duration:
                cable_s = None
    frames = int(duration // interval_s)
    run_alt = alt[:max(stop, 1)]
    return {
        "target": target.summary(), "time": t, "minutes": minutes, "interval_s": interval_s, "frames": frames,
        "playback_s": frames / 25.0,  # a 25 frames/s clip
        "held_for_s": duration if held else sets_in,
        "sets_in_s": sets_in,
        "alt": {"start": float(alt[0]), "end": float(run_alt[-1]), "min": float(run_alt.min()), "max": float(run_alt.max())},
        "az": {"start": float(az[0]), "end": float(az[max(stop, 1) - 1]), "swept": float(abs(unwrapped_az[-1] - unwrapped_az[0]))},
        "field_rotation_deg": rotation,
        "field_rotation_per_frame_deg": rotation / frames if frames else 0.0,
        "star_motion_per_frame_arcsec": 15.041 * interval_s * math.cos(math.radians(float(eng.radec(target, t)["dec_j2000"]))),
        "cable_limit_in_s": cable_s,
        "az_axis_start": az_axis_start,
        "sun_alt": {"start": float(sun_alt[0]), "end": float(sun_alt[-1]), "max": float(sun_alt.max())},
        "dark": bool(sun_alt.max() < -12.0),
        "moon": {"illuminated": eng.moon_phase(t)["illuminated"], "separation_deg": _moon_separation(ctl, target, t)},
        "estimate": "Rotation from the field-rotation rate of an alt-az mount; times from the configured limits, not measured on hardware.",
    }


def _moon_separation(ctl: MountController, target: Target, t: float) -> float | None:
    eng = ctl.engine
    if target.kind in ("moon", "altaz"):
        return None
    ma, mz = (float(x[0]) for x in (eng.altaz(eng.resolve("moon"), np.array([t]))))
    ta, tz = (float(x[0]) for x in (eng.altaz(target, np.array([t]))))
    return float(separation(ma, mz, ta, tz))
