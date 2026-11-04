"""Orbital elements supplied by the user: Earth satellites (TLE) and comets / asteroids (MPC formats).

Everything here works offline. The user pastes or uploads element sets (for example from CelesTrak or the Minor
Planet Center); OpenSkyMount does not download them itself. Elements are kept in ``state/orbits.json`` so they
survive a restart.

Formats:
* TLE: optional name line followed by the two 69-character element lines; checksums are verified.
* MPC comets: one line per comet in the ``CometEls.txt`` format
  (https://www.minorplanetcenter.net/iau/info/CometOrbitFormat.html).
* MPC minor planets: one line per object in the ``MPCORB.DAT`` format
  (https://www.minorplanetcenter.net/iau/info/MPOrbitFormat.html).

Comets and asteroids are propagated as two-body (Keplerian) orbits around the Sun, like Skyfield's own
``skyfield.data.mpc`` helpers (which need pandas, so the column layout is parsed here directly). Planetary
perturbations are ignored: positions are good to arcseconds or arcminutes near the element epoch and degrade
over months. Satellites use SGP4 via Skyfield; TLE accuracy degrades over days to weeks.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

MAX_OBJECTS = 2000


class ElementError(ValueError):
    pass


# ------------------------------------------------------------------- TLE
def tle_checksum(line: str) -> int:
    """Modulo-10 checksum of the first 68 characters ('-' counts as 1, digits as their value)."""
    total = 0
    for ch in line[:68]:
        if ch.isdigit():
            total += int(ch)
        elif ch == "-":
            total += 1
    return total % 10


@dataclass
class SatelliteElements:
    norad: int
    name: str
    line1: str
    line2: str

    @property
    def id(self) -> str:
        return f"sat:{self.norad}"

    @property
    def epoch_unix(self) -> float:
        """TLE epoch (UTC) as a Unix timestamp."""
        yy = int(self.line1[18:20])
        year = 2000 + yy if yy < 57 else 1900 + yy
        day = float(self.line1[20:32])
        import datetime as dt

        start = dt.datetime(year, 1, 1, tzinfo=dt.UTC).timestamp()
        return start + (day - 1.0) * 86400.0


def parse_tle(text: str) -> list[SatelliteElements]:
    lines = [ln.rstrip() for ln in text.replace("\r", "").split("\n")]
    lines = [ln for ln in lines if ln.strip()]
    out: list[SatelliteElements] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("1 ") and i + 1 < len(lines) and lines[i + 1].startswith("2 "):
            name, l1, l2 = None, ln, lines[i + 1]
            i += 2
        elif i + 2 < len(lines) and lines[i + 1].startswith("1 ") and lines[i + 2].startswith("2 "):
            name, l1, l2 = ln.strip(), lines[i + 1], lines[i + 2]
            if name.startswith("0 "):  # 3LE format: name line prefixed with "0 "
                name = name[2:].strip()
            i += 3
        else:
            raise ElementError(f"line {i + 1}: expected a TLE (optional name line, then lines '1 …' and '2 …')")
        l1, l2 = l1.strip(), l2.strip()
        if len(l1) < 69 or len(l2) < 69:
            raise ElementError(f"{name or l1[:20]}: TLE lines must be 69 characters long")
        for n, line in ((1, l1), (2, l2)):
            if not line[68].isdigit() or int(line[68]) != tle_checksum(line):
                raise ElementError(f"{name or l1[:20]}: checksum error in line {n}")
        try:
            norad = int(l1[2:7])
        except ValueError:
            raise ElementError(f"{name or l1[:20]}: alpha-5 catalog numbers are not supported") from None
        if l2[2:7].strip() != l1[2:7].strip():
            raise ElementError(f"{name or norad}: catalog numbers of lines 1 and 2 differ")
        out.append(SatelliteElements(norad, name or f"NORAD {norad}", l1[:69], l2[:69]))
    if not out:
        raise ElementError("no TLE found")
    return out


# ------------------------------------------------------------------- MPC
def _packed_digit(c: str) -> int:
    return ord(c) - (48 if c.isdigit() else 55)


def unpack_epoch(packed: str) -> tuple[int, int, float]:
    """MPC packed date (e.g. ``K2555``) -> (year, month, day)."""
    if len(packed) < 5:
        raise ElementError(f"bad packed epoch {packed!r}")
    try:
        year = 100 * _packed_digit(packed[0]) + int(packed[1:3])
        month, day = _packed_digit(packed[3]), _packed_digit(packed[4])
    except ValueError:
        raise ElementError(f"bad packed epoch {packed!r}") from None
    if not (1 <= month <= 12 and 1 <= day <= 31):
        raise ElementError(f"bad packed epoch {packed!r}")
    return year, month, float(day)


@dataclass
class MinorBodyElements:
    """Heliocentric two-body elements referred to the J2000 ecliptic and equinox."""

    kind: str  # comet | asteroid
    key: str  # short designation, unique per kind
    name: str
    q: float  # perihelion distance (AU)
    e: float
    i: float
    node: float
    peri: float
    # Comets: time of perihelion (TT); asteroids: mean anomaly at the epoch (TT).
    tp_year: int = 0
    tp_month: int = 0
    tp_day: float = 0.0
    mean_anomaly: float | None = None
    epoch_year: int = 0
    epoch_month: int = 0
    epoch_day: float = 0.0
    # Magnitude parameters: comets g, k (m = g + 5 log Δ + 2.5 k log r); asteroids H, G.
    mag1: float | None = None
    mag2: float | None = None
    line: str = ""

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.key}"

    @property
    def semilatus_rectum(self) -> float:
        if abs(self.e - 1.0) < 1e-12:
            return 2.0 * self.q
        return self.q * (1.0 + self.e)


def _num(text: str, what: str) -> float:
    try:
        v = float(text)
    except ValueError:
        raise ElementError(f"cannot read {what} from {text!r}") from None
    if not math.isfinite(v):
        raise ElementError(f"{what} is not finite")
    return v


def _opt(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


def parse_comet_line(line: str) -> MinorBodyElements:
    if len(line) < 103:
        raise ElementError("comet line too short (CometEls.txt format expected)")
    number, otype, packed = line[0:4].strip(), line[4:5].strip(), line[5:12].strip()
    designation = line[102:158].strip()
    e = _num(line[41:49], "eccentricity")
    q = _num(line[30:39], "perihelion distance")
    if q <= 0 or e < 0:
        raise ElementError("perihelion distance must be > 0 and eccentricity >= 0")
    key = designation.split("(")[0].strip() if designation else (number.lstrip("0") + otype if number else packed)
    return MinorBodyElements(
        kind="comet", key=key or packed, name=designation or key,
        q=q, e=e, peri=_num(line[51:59], "argument of perihelion"), node=_num(line[61:69], "ascending node"),
        i=_num(line[71:79], "inclination"),
        tp_year=int(_num(line[14:18], "perihelion year")), tp_month=int(_num(line[19:21], "perihelion month")),
        tp_day=_num(line[22:29], "perihelion day"),
        mag1=_opt(line[91:95]), mag2=_opt(line[96:100]), line=line.rstrip(),
    )


def parse_mpcorb_line(line: str) -> MinorBodyElements:
    if len(line) < 103:
        raise ElementError("minor-planet line too short (MPCORB.DAT format expected)")
    a = _num(line[92:103], "semi-major axis")
    e = _num(line[70:79], "eccentricity")
    if not (a > 0 and 0 <= e < 1):
        raise ElementError("MPCORB elements need a > 0 and 0 <= e < 1")
    year, month, day = unpack_epoch(line[20:25].strip())
    readable = line[166:194].strip()
    packed = line[0:7].strip()
    m = re.match(r"\((\d+)\)\s*(.*)", readable)
    if m:
        key, name = m.group(1), f"({m.group(1)}) {m.group(2)}".strip()
    else:
        key, name = (readable or packed), (readable or packed)
    return MinorBodyElements(
        kind="asteroid", key=key, name=name, q=a * (1.0 - e), e=e,
        mean_anomaly=_num(line[26:35], "mean anomaly"), peri=_num(line[37:46], "argument of perihelion"),
        node=_num(line[48:57], "ascending node"), i=_num(line[59:68], "inclination"),
        epoch_year=year, epoch_month=month, epoch_day=day,
        mag1=_opt(line[8:13]), mag2=_opt(line[14:19]), line=line.rstrip(),
    )


def _looks_like_comet(line: str) -> bool:
    return len(line) >= 103 and line[4:5] in "PCDXIA" and line[14:18].strip().isdigit() and line[19:21].strip().isdigit()


def parse_mpc(text: str) -> list[MinorBodyElements]:
    out: list[MinorBodyElements] = []
    for n, raw in enumerate(text.replace("\r", "").split("\n"), start=1):
        if not raw.strip() or set(raw.strip()) <= {"-"}:
            continue
        if raw.startswith(("Des'n", "MINOR PLANET", "Des'n ")):
            continue
        try:
            out.append(parse_comet_line(raw) if _looks_like_comet(raw) else parse_mpcorb_line(raw))
        except ElementError as exc:
            raise ElementError(f"line {n}: {exc}") from None
    if not out:
        raise ElementError("no MPC orbit lines found")
    return out


def parse_any(text: str) -> tuple[list[SatelliteElements], list[MinorBodyElements]]:
    """TLE if the text contains TLE element lines, MPC formats otherwise."""
    if re.search(r"(^|\n)1 [ 0-9]{5}[A-Z ] ", text) and re.search(r"(^|\n)2 [ 0-9]{5} ", text):
        return parse_tle(text), []
    return [], parse_mpc(text)


# ------------------------------------------------------------------- store
class OrbitStore:
    """User-supplied element sets, keyed by target id, kept in insertion order."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.satellites: dict[str, SatelliteElements] = {}
        self.minor: dict[str, MinorBodyElements] = {}
        self.revision = 0
        if path and path.exists():
            try:
                data = json.loads(path.read_text())
                for s in data.get("satellites", []):
                    sat = SatelliteElements(**s)
                    self.satellites[sat.id] = sat
                for m in data.get("minor", []):
                    mb = MinorBodyElements(**m)
                    self.minor[mb.id] = mb
            except (ValueError, TypeError, KeyError):
                pass  # unreadable file: start empty rather than refusing to start

    def __len__(self) -> int:
        return len(self.satellites) + len(self.minor)

    def add_text(self, text: str) -> dict[str, Any]:
        if len(text) > 5_000_000:
            raise ElementError("text too large (limit 5 MB)")
        sats, minor = parse_any(text)
        if len(self) + len(sats) + len(minor) > MAX_OBJECTS + sum(
            1 for s in sats if s.id in self.satellites) + sum(1 for m in minor if m.id in self.minor):
            raise ElementError(f"at most {MAX_OBJECTS} objects can be loaded; remove some first")
        for s in sats:
            self.satellites[s.id] = s
        for m in minor:
            self.minor[m.id] = m
        self._changed()
        return {"added": [s.id for s in sats] + [m.id for m in minor]}

    def remove(self, target_id: str) -> bool:
        found = self.satellites.pop(target_id, None) or self.minor.pop(target_id, None)
        if found:
            self._changed()
        return found is not None

    def clear(self) -> None:
        self.satellites.clear()
        self.minor.clear()
        self._changed()

    def _changed(self) -> None:
        self.revision += 1
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({
                "satellites": [asdict(s) for s in self.satellites.values()],
                "minor": [asdict(m) for m in self.minor.values()],
            }))

    def get(self, target_id: str) -> SatelliteElements | MinorBodyElements | None:
        return self.satellites.get(target_id) or self.minor.get(target_id)

    def find(self, kind: str, key: str) -> SatelliteElements | MinorBodyElements | None:
        k = key.strip().lower()
        pool = self.satellites.values() if kind == "sat" else (m for m in self.minor.values() if m.kind == kind)
        for o in pool:
            okey = str(o.norad) if isinstance(o, SatelliteElements) else o.key
            if okey.lower() == k:
                return o
        return None

    def search(self, q: str, limit: int) -> list[dict[str, Any]]:
        ql = q.strip().lower()
        hits: list[dict[str, Any]] = []
        pool: list[SatelliteElements | MinorBodyElements] = [*self.satellites.values(), *self.minor.values()]
        for o in pool:
            name = o.name.lower()
            key = str(o.norad) if isinstance(o, SatelliteElements) else o.key.lower()
            if ql in (name, key):
                score = 100
            elif name.startswith(ql) or key.startswith(ql):
                score = 80
            elif ql in name:
                score = 60
            else:
                continue
            kind = "satellite" if isinstance(o, SatelliteElements) else o.kind
            detail = f"NORAD {o.norad}" if isinstance(o, SatelliteElements) else "MPC elements"
            hits.append({"id": o.id, "name": o.name, "kind": kind, "magnitude": None, "detail": detail, "score": score})
        hits.sort(key=lambda h: -h["score"])
        return hits[:limit]

    def describe(self, now: float) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for s in self.satellites.values():
            out.append({"id": s.id, "name": s.name, "kind": "satellite", "norad": s.norad,
                        "epoch": s.epoch_unix, "age_days": (now - s.epoch_unix) / 86400.0})
        for m in self.minor.values():
            out.append({"id": m.id, "name": m.name, "kind": m.kind, "q": m.q, "e": m.e, "i": m.i})
        return out
