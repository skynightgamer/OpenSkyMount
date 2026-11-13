"""Slew preview: what a GoTo to a target would do, before it is sent.

Read-only: nothing is commanded and no controller state changes. It answers the questions a person asks before
pressing GoTo: will it be allowed, how long will it take, which way do the axes turn, where will the cable wrap be
afterwards, and for how long can the target be tracked before the cable limit is reached.

The duration is an estimate from the configured axis limits (a time-optimal trapezoid per axis, as the motion model
and typical firmware do) plus a short settling time; real hardware has not been measured. For a moving target the
position at the arrival time is found by a few fixed-point iterations.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ..astro.engine import TargetError
from .controller import CommandError, MountController
from .geometry import separation

SETTLE_S = 1.0  # settling inside the GoTo tolerance after the axes arrive
PATH_POINTS = 48
NEAR_LIMIT_DEG = 40.0  # a target this close to a cable limit gets a warning
TRACK_WARN_S = 3 * 3600.0  # warn when tracking would hit the cable limit sooner than this
TRACK_HORIZON_S = 6 * 3600.0  # how far ahead the cable limit is looked for


def axis_time(distance: float, vmax: float, accel: float) -> float:
    """Time (s) to travel ``distance`` degrees from rest to rest with a rate and acceleration limit."""
    d = abs(distance)
    if d <= 0.0:
        return 0.0
    if d >= vmax * vmax / accel:  # reaches full speed
        return d / vmax + vmax / accel
    return 2.0 * math.sqrt(d / accel)


def axis_travel(distance: float, vmax: float, accel: float, t: float) -> float:
    """Distance covered after ``t`` seconds of the same move (signed like ``distance``)."""
    d = abs(distance)
    total = axis_time(d, vmax, accel)
    t = max(0.0, min(t, total))
    if d >= vmax * vmax / accel:
        t_acc = vmax / accel
        if t < t_acc:
            x = 0.5 * accel * t * t
        elif t <= total - t_acc:
            x = 0.5 * accel * t_acc * t_acc + vmax * (t - t_acc)
        else:
            rest = total - t
            x = d - 0.5 * accel * rest * rest
    else:
        half = total / 2.0
        x = 0.5 * accel * t * t if t <= half else d - 0.5 * accel * (total - t) ** 2
    return math.copysign(x, distance)


def _tracking_time_to_cable_limit(ctl: MountController, target: Any, t_start: float, az_start: float) -> float | None:
    """Seconds after ``t_start`` until tracking ``target`` from azimuth axis ``az_start`` hits a cable-wrap limit.

    None when it does not within TRACK_HORIZON_S, when the target sets (altitude limit) first, or for fixed horizon
    positions, which are not tracked. The azimuth is followed with the astronomy engine every minute (its rate is not
    constant over hours), on the same turn of the cable as the GoTo.
    """
    if target.kind == "altaz":
        return None
    lim = ctl.limits
    times = t_start + np.arange(0.0, TRACK_HORIZON_S + 1.0, 60.0)
    alt, az = ctl.engine.altaz(target, times)
    below = np.nonzero(alt < ctl.config.control.min_target_altitude)[0]
    n = int(below[0]) if len(below) else len(times)
    if n < 2:
        return None
    unwrapped = np.degrees(np.unwrap(np.radians(az[:n])))
    axis = az_start + (unwrapped - unwrapped[0])
    out = np.nonzero((axis > lim.az_max) | (axis < lim.az_min))[0]
    if len(out) == 0:
        return None
    k = int(out[0])
    if k == 0:
        return 0.0
    edge = lim.az_max if axis[k] > lim.az_max else lim.az_min
    frac = (edge - axis[k - 1]) / (axis[k] - axis[k - 1])
    return float((k - 1 + frac) * 60.0)


def _describe_turn(delta_az: float) -> str:
    if abs(delta_az) < 0.05:
        return "no azimuth movement"
    return f"{abs(delta_az):.0f}° {'clockwise (towards east)' if delta_az > 0 else 'counter-clockwise (towards west)'}"


def slew_preview(ctl: MountController, target_id: str) -> dict[str, Any]:
    """The plan for a GoTo to ``target_id`` from the mount's present position (see the module docstring)."""
    try:
        target = ctl.engine.resolve(target_id)
    except TargetError as exc:
        raise CommandError(str(exc)) from exc
    out: dict[str, Any] = {"target": target.summary(), "refused": None, "warnings": []}
    try:
        ctl._require_operational()
        t_alt, t_az = ctl._check_goto(target)
    except CommandError as exc:
        out["refused"] = str(exc)
        return out

    lim = ctl.limits
    st = ctl.driver.status()
    cur_alt_axis, cur_az_axis = st.alt.position, st.az.position
    cur_sky = ctl.pointing()
    t_now = ctl.sim_time

    # Where the axes have to go; for a moving target, where it will be when they arrive.
    duration = 0.0
    cmd_alt = cur_alt_axis
    az_cmd = cur_az_axis
    sky_alt, sky_az = t_alt, t_az
    for _ in range(4):
        a, z, _, _ = ctl.engine.altaz_rates(target, t_now + duration)
        if not (math.isfinite(a) and math.isfinite(z)):
            out["refused"] = f"no valid position for {target.name} at the arrival time"
            return out
        sky_alt, sky_az = a, z
        cmd_alt, m_az = ctl.model.sky_to_mount(a, z)
        picked = ctl._choose_az(target, m_az, cur_az_axis)
        if picked is None:
            out["refused"] = "azimuth not reachable within cable-wrap limits"
            return out
        az_cmd = picked
        t_alt_axis = axis_time(cmd_alt - cur_alt_axis, lim.max_rate, lim.max_accel)
        t_az_axis = axis_time(az_cmd - cur_az_axis, lim.max_rate, lim.max_accel)
        new = max(t_alt_axis, t_az_axis) + SETTLE_S
        if abs(new - duration) < 0.05:
            duration = new
            break
        duration = new
    d_alt = cmd_alt - cur_alt_axis
    d_az = az_cmd - cur_az_axis

    # Path of the pointing direction: both axes move at once, each with its own trapezoid.
    path = []
    t_end = max(axis_time(d_alt, lim.max_rate, lim.max_accel), axis_time(d_az, lim.max_rate, lim.max_accel))
    for t in np.linspace(0.0, t_end, PATH_POINTS):
        alt_t = cur_alt_axis + axis_travel(d_alt, lim.max_rate, lim.max_accel, float(t))
        az_t = cur_az_axis + axis_travel(d_az, lim.max_rate, lim.max_accel, float(t))
        a, z = ctl.model.mount_to_sky(alt_t, az_t)
        path.append([round(a, 4), round(z % 360.0, 4)])

    # Cable wrap: the azimuth axis is multi-turn inside [az_min, az_max]; 0 is the neutral position.
    span = lim.az_max - lim.az_min
    margin_now = min(cur_az_axis - lim.az_min, lim.az_max - cur_az_axis)
    margin_after = min(az_cmd - lim.az_min, lim.az_max - az_cmd)
    long_way = abs(d_az) > 180.0 + 1e-6
    warnings: list[str] = out["warnings"]
    if long_way:
        warnings.append(f"The azimuth turns {abs(d_az):.0f}° the long way round: the short way would pass the cable-wrap limit.")
    if margin_after < NEAR_LIMIT_DEG:
        warnings.append(f"The GoTo ends {margin_after:.0f}° from a cable-wrap limit.")
    track_limit_s = _tracking_time_to_cable_limit(ctl, target, t_now + duration, az_cmd)
    if track_limit_s is not None and track_limit_s < TRACK_WARN_S:
        how = (f"The mount will unwind 360° by itself (a pause of about {axis_time(360.0, lim.max_rate, lim.max_accel):.0f} s)."
               if ctl.config.control.auto_unwind else "Tracking will stop there (automatic unwinding is off).")
        warnings.append(f"Tracking would reach the cable-wrap limit in about {track_limit_s / 60:.0f} min. {how}")

    out.update({
        "refused": None,
        "from": {"alt": cur_sky[0], "az": cur_sky[1], "alt_axis": cur_alt_axis, "az_axis": cur_az_axis},
        "to": {"alt": sky_alt, "az": sky_az, "alt_axis": cmd_alt, "az_axis": az_cmd},
        "delta": {"alt": d_alt, "az": d_az},
        "turn": {"alt": "up" if d_alt > 0.05 else "down" if d_alt < -0.05 else "none", "az": _describe_turn(d_az)},
        "separation_deg": separation(cur_sky[0], cur_sky[1], sky_alt, sky_az),
        "duration_s": duration,
        "estimate": "From the configured axis limits (trapezoid per axis) plus settling; not measured on hardware.",
        "path": path,
        "cable": {
            "az_min": lim.az_min, "az_max": lim.az_max, "span": span, "now": cur_az_axis, "after": az_cmd,
            "margin_now": margin_now, "margin_after": margin_after, "long_way": long_way,
            "tracking_limit_s": track_limit_s,
        },
    })
    return out
