"""Angle helpers and alt-az geometry."""

from __future__ import annotations

import math

import numpy as np


def wrap360(a: float) -> float:
    return a % 360.0


def wrap180(a: float) -> float:
    return (a + 180.0) % 360.0 - 180.0


def altaz_to_vec(alt: float, az: float) -> np.ndarray:
    """Unit vector in the local (North, East, Up) frame."""
    a, z = math.radians(alt), math.radians(az)
    return np.array([math.cos(a) * math.cos(z), math.cos(a) * math.sin(z), math.sin(a)])


def vec_to_altaz(v: np.ndarray) -> tuple[float, float]:
    n = v / np.linalg.norm(v)
    alt = math.degrees(math.asin(max(-1.0, min(1.0, float(n[2])))))
    az = math.degrees(math.atan2(float(n[1]), float(n[0]))) % 360.0
    return alt, az


def separation(alt1: float, az1: float, alt2: float, az2: float) -> float:
    """Great-circle angle (deg) between two horizon directions (haversine, stable for small angles)."""
    p1, p2 = math.radians(alt1), math.radians(alt2)
    dl = math.radians(az2 - az1)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(h))))


def nearest_unwrapped(az: float, current: float, az_min: float, az_max: float) -> float | None:
    """Pick the multi-turn azimuth equivalent of ``az`` closest to ``current`` within limits."""
    base = current + wrap180(az - current)
    best: float | None = None
    for k in (-2, -1, 0, 1, 2):
        cand = base + 360.0 * k
        if az_min <= cand <= az_max and (best is None or abs(cand - current) < abs(best - current)):
            best = cand
    return best


def rotation_about(axis: np.ndarray, angle_deg: float) -> np.ndarray:
    axis = axis / np.linalg.norm(axis)
    a = math.radians(angle_deg)
    k = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    return np.eye(3) + math.sin(a) * k + (1 - math.cos(a)) * (k @ k)


def tilt_matrix(magnitude: float, direction_az: float) -> np.ndarray:
    """Rotation describing a mount base that dips by ``magnitude`` towards ``direction_az``."""
    if magnitude == 0.0:
        return np.eye(3)
    d = math.radians(direction_az)
    # Horizontal axis perpendicular to the dip direction; rotating about it lowers that side.
    axis = np.array([-math.sin(d), math.cos(d), 0.0])
    return rotation_about(axis, magnitude)


def field_rotation_rate(lat: float, alt: float, az: float, sidereal_rate: float = 360.0 / 86164.0905) -> float:
    ca = math.cos(math.radians(alt))
    if abs(ca) < 1e-9:
        return math.inf
    return sidereal_rate * math.cos(math.radians(lat)) * math.cos(math.radians(az)) / ca
