"""LX200 command helpers (Meade LX200 subset plus OnStep extensions) shared by the OnStep driver and its fake.

Commands start with ``:`` and end with ``#``. Replies come in three shapes, which the client must know per command:
nothing (motion and rate commands), a single character without terminator (``:Sa``, ``:Sz``, ``:MA``, ``:Te``,
``:Td`` and the ACK byte), or text terminated by ``#`` (all ``:G..`` getters).

Only the subset OpenSkyMount needs is described here. It follows the Meade LX200 command reference and OnStep's
command list; it has **not** been checked against real OnStepX firmware (no hardware exists for this project yet).
"""

from __future__ import annotations

import math
import re

ACK = b"\x06"  # answered with the mount mode: A = alt-az, P = polar (equatorial fork), G = German equatorial, L = land

# Direction letters for manual moves (:M?# / :Q?#). Assumption to verify on hardware: on an alt-az OnStep, "west"
# moves axis 1 (azimuth) forward (increasing azimuth) as it does on equatorial mounts (increasing hour angle), and
# "north" raises the altitude axis. The OnStep driver checks the measured direction at run time and faults if it is
# reversed, instead of trusting this table.
AZ_PLUS, AZ_MINUS = "w", "e"
ALT_PLUS, ALT_MINUS = "n", "s"

# :MA# (GoTo alt/az target) replies, as in OnStep's GoTo error codes.
GOTO_ERRORS = {
    "1": "below the horizon limit",
    "2": "above the overhead limit",
    "3": "controller in standby",
    "4": "mount is parked",
    "5": "a GoTo is already in progress",
    "6": "outside the axis limits",
    "7": "hardware fault",
    "8": "mount is already moving",
    "9": "unspecified error",
}

_ANGLE = re.compile(r"^\s*([+-]?)(\d{1,3})[*:\xdf\xb0](\d{1,2})(?:[':](\d{1,2}(?:\.\d+)?))?\s*$")


class LX200Error(Exception):
    pass


def parse_angle(text: str) -> float:
    """``sDD*MM``, ``sDD*MM'SS``, ``DDD*MM:SS(.s)`` (``*``, ``:``, ``ß`` or ``°`` after the degrees) to degrees."""
    m = _ANGLE.match(text)
    if not m:
        raise LX200Error(f"not an LX200 angle: {text!r}")
    sign, d, mi, sec = m.groups()
    minutes, seconds = int(mi), float(sec) if sec else 0.0
    if minutes >= 60 or seconds >= 60:
        raise LX200Error(f"not an LX200 angle: {text!r}")
    value = int(d) + minutes / 60 + seconds / 3600
    return -value if sign == "-" else value


def has_seconds(text: str) -> bool:
    m = _ANGLE.match(text)
    return bool(m and m.group(4) is not None)


def _dms(value: float) -> tuple[int, int, int]:
    total = int(round(abs(value) * 3600))
    return total // 3600, (total // 60) % 60, total % 60


def format_alt(deg: float) -> str:
    """High-precision altitude ``sDD*MM'SS`` (as sent with :Sa and returned by :GA)."""
    if not math.isfinite(deg) or abs(deg) > 90:
        raise LX200Error(f"altitude out of range: {deg}")
    d, m, s = _dms(deg)
    return f"{'-' if deg < 0 and (d or m or s) else '+'}{d:02d}*{m:02d}'{s:02d}"


def format_az(deg: float) -> str:
    """High-precision azimuth ``DDD*MM'SS`` in [0, 360)."""
    if not math.isfinite(deg):
        raise LX200Error(f"azimuth not finite: {deg}")
    d, m, s = _dms(deg % 360.0)
    return f"{d % 360:03d}*{m:02d}'{s:02d}"


def format_rate(deg_per_s: float) -> str:
    """Rate argument for :RA / :RE (degrees per second; the sign is given by the move direction)."""
    return f"{abs(deg_per_s):.6f}".rstrip("0").rstrip(".") or "0"
