"""Telemetry recording for later replay and analysis of tracking errors.

A recording is a JSON Lines file in ``state/logs/``: one header line, then samples (``"k": "s"``) and controller
log events (``"k": "e"``). Samples are taken from the same controller snapshot that is broadcast to the clients,
at most ``rate_hz`` times per wall-clock second. Keys are short to keep files small (~150 bytes per sample,
about 2.5 MB per hour at 5 Hz).

Sample keys: ``w`` wall time, ``t`` simulation time, ``m`` mode, ``id`` target id, ``ta``/``tz`` target alt/az,
``a``/``z`` estimated pointing alt/az, ``xa``/``xz`` raw axis angles, ``ra``/``rz`` axis rates (deg/s), ``e``
pointing error reported by the mount (deg), ``te`` true error (simulator ground truth, deg), ``f`` flags.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import IO, Any

from .. import __version__

MAX_BYTES = 200_000_000
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}\.jsonl$")


class RecorderError(ValueError):
    pass


def _r(x: Any, nd: int) -> Any:
    return round(x, nd) if isinstance(x, float) else x


class TelemetryRecorder:
    def __init__(self, directory: Path | None, rate_hz: float = 5.0) -> None:
        self.dir = directory
        self.rate_hz = rate_hz
        self._fh: IO[str] | None = None
        self.name: str | None = None
        self.samples = 0
        self.bytes = 0
        self.started_wall: float | None = None
        self._last_wall = -1e18
        self._seen_events: set[tuple[float, str]] = set()

    @property
    def active(self) -> bool:
        return self._fh is not None

    def start(self, wall: float, label: str = "", header: dict[str, Any] | None = None) -> str:
        if self.dir is None:
            raise RecorderError("recording needs a state directory")
        if self.active:
            return self.name or ""
        self.dir.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.fromtimestamp(wall, dt.UTC).strftime("%Y%m%d-%H%M%S")
        slug = re.sub(r"[^A-Za-z0-9_-]+", "-", label.strip())[:40].strip("-")
        name = f"{stamp}{'-' + slug if slug else ''}.jsonl"
        n = 1
        while (self.dir / name).exists():
            n += 1
            name = f"{stamp}{'-' + slug if slug else ''}-{n}.jsonl"
        self._fh = (self.dir / name).open("w", encoding="utf-8")
        self.name, self.samples, self.bytes, self.started_wall = name, 0, 0, wall
        self._last_wall = -1e18
        self._seen_events = set()
        self._write({"k": "h", "format": "openskymount-telemetry/1", "version": __version__, "wall": wall,
                     "label": label, "rate_hz": self.rate_hz, **(header or {})})
        return name

    def stop(self) -> str | None:
        """Close the file; returns its name, or None if nothing was being recorded."""
        if self._fh is None:
            return None
        self._fh.close()
        self._fh = None
        return self.name

    def _write(self, obj: dict[str, Any]) -> None:
        assert self._fh is not None
        line = json.dumps(obj, separators=(",", ":"), allow_nan=False) + "\n"
        self._fh.write(line)
        self.bytes += len(line)
        if self.bytes > MAX_BYTES:
            self._fh.write(json.dumps({"k": "e", "t": None, "level": "warning", "msg": "size limit reached"}) + "\n")
            self.stop()

    def record(self, mount: dict[str, Any], wall: float) -> None:
        """Add a sample from a (NaN-free) controller snapshot, rate limited in wall time."""
        if not self.active or wall - self._last_wall < 1.0 / self.rate_hz - 1e-6:
            return
        self._last_wall = wall
        for ev in mount.get("events", []):
            key = (ev["t"], ev["msg"])
            if key not in self._seen_events:
                self._seen_events.add(key)
                self._write({"k": "e", "t": ev["t"], "level": ev["level"], "msg": ev["msg"]})
                if not self.active:
                    return
        if len(self._seen_events) > 500:  # the snapshot only carries the last 15 events
            self._seen_events = {(e["t"], e["msg"]) for e in mount.get("events", [])}
        p, tg = mount["position"], mount.get("target")
        self._write({
            "k": "s", "w": round(wall, 3), "t": round(mount["time"], 3), "m": mount["mode"],
            "id": tg["id"] if tg else None, "ta": _r(tg["alt"], 5) if tg else None, "tz": _r(tg["az"], 5) if tg else None,
            "a": _r(p["alt"], 5), "z": _r(p["az"], 5), "xa": _r(p["alt_axis"], 5), "xz": _r(p["az_axis"], 5),
            "ra": _r(p["alt_rate"], 6), "rz": _r(p["az_rate"], 6),
            "e": _r(mount.get("tracking_error"), 6), "te": _r(mount.get("true_pointing_error"), 6),
            "f": mount.get("flags") or [],
        })
        if self.active:
            self.samples += 1

    def describe(self) -> dict[str, Any]:
        return {"active": self.active, "name": self.name if self.active else None, "samples": self.samples,
                "bytes": self.bytes}

    # ------------------------------------------------------------------ files
    def list(self) -> list[dict[str, Any]]:
        if self.dir is None or not self.dir.exists():
            return []
        out = []
        for f in sorted(self.dir.glob("*.jsonl"), reverse=True):
            st = f.stat()
            out.append({"name": f.name, "bytes": st.st_size, "modified": st.st_mtime,
                        "recording": self.active and f.name == self.name})
        return out

    def path(self, name: str) -> Path:
        if self.dir is None or not NAME_RE.match(name):
            raise RecorderError("invalid log name")
        p = self.dir / name
        if not p.is_file():
            raise RecorderError("no such log")
        return p

    def delete(self, name: str) -> None:
        if self.active and name == self.name:
            raise RecorderError("stop the recording before deleting it")
        self.path(name).unlink()
