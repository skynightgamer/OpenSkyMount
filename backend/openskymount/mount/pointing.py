"""Pointing model: maps mount (axis) coordinates to sky directions and back.

Model (all angles in degrees)::

    sky = R(tilt_n, tilt_e) · dir(alt_mount + alt_off, az_mount + az_off)

* ``alt_off`` / ``az_off`` – zero-point errors of the altitude and azimuth axes
  (encoder zero not exactly level / not exactly at true north).
* ``tilt_n`` / ``tilt_e`` – small rotations of the whole mount about the local North and
  East axes: a base that is not level.

Fitted by least squares (Gauss-Newton) from alignment points, each pairing a raw mount
reading with the sky direction that was actually observed there (a centred star for
Sync, or a plate solution). With 1–2 points only the zero offsets are fitted; from
3 points the base tilt is fitted as well. Non-perpendicularity and cone error are not
modelled (see docs/limitations.md).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from .geometry import altaz_to_vec, rotation_about, vec_to_altaz, wrap180

NORTH = np.array([1.0, 0.0, 0.0])
EAST = np.array([0.0, 1.0, 0.0])
TILT_MIN_POINTS = 3


@dataclass
class AlignmentPoint:
    mount_alt: float
    mount_az: float
    sky_alt: float
    sky_az: float
    label: str = ""
    time: float = 0.0
    source: str = "sync"  # sync | plate_solve


@dataclass
class PointingModel:
    alt_off: float = 0.0
    az_off: float = 0.0
    tilt_n: float = 0.0
    tilt_e: float = 0.0
    points: list[AlignmentPoint] = field(default_factory=list)

    # ---------------------------------------------------------------- mapping
    def _rotation(self, p: np.ndarray | None = None) -> np.ndarray:
        tn, te = (self.tilt_n, self.tilt_e) if p is None else (p[2], p[3])
        if tn == 0.0 and te == 0.0:
            return np.eye(3)
        return rotation_about(NORTH, tn) @ rotation_about(EAST, te)

    def mount_to_sky(self, alt: float, az: float) -> tuple[float, float]:
        if self.is_identity():
            return alt, az % 360.0
        return self._forward(np.array([self.alt_off, self.az_off, self.tilt_n, self.tilt_e]), alt, az)

    def _forward(self, p: np.ndarray, alt: float, az: float) -> tuple[float, float]:
        return vec_to_altaz(self._rotation(p) @ altaz_to_vec(alt + p[0], az + p[1]))

    def sky_to_mount(self, alt: float, az: float) -> tuple[float, float]:
        if self.is_identity():
            return alt, az % 360.0
        a, z = vec_to_altaz(self._rotation().T @ altaz_to_vec(alt, az))
        return a - self.alt_off, (z - self.az_off) % 360.0

    def is_identity(self) -> bool:
        return self.alt_off == 0.0 and self.az_off == 0.0 and self.tilt_n == 0.0 and self.tilt_e == 0.0

    # ---------------------------------------------------------------- points
    def add(self, point: AlignmentPoint) -> None:
        self.points.append(point)
        self.fit()

    def remove(self, index: int) -> None:
        if not 0 <= index < len(self.points):
            raise IndexError("no alignment point with that index")
        del self.points[index]
        self.fit()

    def clear(self) -> None:
        self.points.clear()
        self.alt_off = self.az_off = self.tilt_n = self.tilt_e = 0.0

    def shift_offsets(self, d_alt: float, d_az: float) -> None:
        """Apply a measured correction without an alignment point (legacy single correction)."""
        self.alt_off -= d_alt
        self.az_off -= d_az

    # ---------------------------------------------------------------- fitting
    @property
    def terms(self) -> str:
        if not self.points and self.is_identity():
            return "none"
        return "offsets + base tilt" if len(self.points) >= TILT_MIN_POINTS else "zero offsets"

    def _residuals(self, p: np.ndarray) -> np.ndarray:
        out = []
        for pt in self.points:
            a, z = self._forward(p, pt.mount_alt, pt.mount_az)
            out.append(a - pt.sky_alt)
            out.append(wrap180(z - pt.sky_az) * math.cos(math.radians(pt.sky_alt)))
        return np.array(out)

    def fit(self, iterations: int = 12) -> None:
        if not self.points:
            self.clear()
            return
        free = [0, 1, 2, 3] if len(self.points) >= TILT_MIN_POINTS else [0, 1]
        p = np.zeros(4)
        h = 1e-4
        for _ in range(iterations):
            r = self._residuals(p)
            jac = np.empty((len(r), len(free)))
            for j, k in enumerate(free):
                dp = p.copy()
                dp[k] += h
                jac[:, j] = (self._residuals(dp) - r) / h
            step, *_ = np.linalg.lstsq(jac, -r, rcond=None)
            for j, k in enumerate(free):
                p[k] += step[j]
            if np.max(np.abs(step)) < 1e-9:
                break
        self.alt_off, self.az_off, self.tilt_n, self.tilt_e = (float(x) for x in p)

    def point_residuals(self) -> list[float]:
        """On-sky error (deg) of each point under the current model."""
        p = np.array([self.alt_off, self.az_off, self.tilt_n, self.tilt_e])
        r = self._residuals(p)
        return [float(math.hypot(r[2 * i], r[2 * i + 1])) for i in range(len(self.points))]

    def rms(self) -> float | None:
        res = self.point_residuals()
        return float(math.sqrt(sum(x * x for x in res) / len(res))) if res else None

    def describe(self) -> dict[str, Any]:
        res = self.point_residuals()
        return {
            "terms": self.terms,
            "alt_off": self.alt_off,
            "az_off": self.az_off,
            "tilt_n": self.tilt_n,
            "tilt_e": self.tilt_e,
            "tilt": math.hypot(self.tilt_n, self.tilt_e),
            "rms": self.rms(),
            "points": [{**asdict(pt), "residual": r} for pt, r in zip(self.points, res, strict=True)],
            "tilt_min_points": TILT_MIN_POINTS,
        }
