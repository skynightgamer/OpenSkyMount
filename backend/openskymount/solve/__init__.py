"""Experimental camera alignment / plate solving proof of concept.

Optional dependency: ``cedar-solve`` (the maintained fork of ESA's tetra3,
Apache-2.0) with its bundled Hipparcos pattern database (FOV 10-30 deg).
Install with ``pip install --no-deps cedar-solve scipy pillow`` (its declared pins
on old numpy/Pillow are stricter than necessary). The rest of OpenSkyMount works
without it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import numpy as np

from ..astro.catalog import Catalog


class SolverUnavailable(RuntimeError):
    pass


@dataclass
class CameraModel:
    width: int = 1024
    height: int = 768
    fov_deg: float = 20.0  # horizontal field of view
    psf_sigma_px: float = 1.3
    noise: float = 3.0  # background noise (counts)
    background: float = 20.0
    mag_limit: float = 6.0


def render_star_field(catalog: Catalog, ra_deg: float, dec_deg: float, roll_deg: float = 0.0,
                      camera: CameraModel | None = None, seed: int = 1, epoch_year: float = 2026.0) -> np.ndarray:
    """Synthetic 8-bit sky image centred on (ra, dec), north up rotated by ``roll`` (gnomonic, sky parity)."""
    cam = camera or CameraModel()
    rng = np.random.default_rng(seed)
    img = rng.normal(cam.background, cam.noise, (cam.height, cam.width))
    ra0, dec0 = math.radians(ra_deg), math.radians(dec_deg)
    scale = (cam.width / 2) / math.tan(math.radians(cam.fov_deg) / 2)
    cr, sr = math.cos(math.radians(roll_deg)), math.sin(math.radians(roll_deg))
    yy, xx = np.mgrid[-6:7, -6:7]
    for s in catalog.stars:
        if s["mag"] > cam.mag_limit:
            break
        ra_e, dec_e = catalog.position_at(s, epoch_year)
        ra, dec = math.radians(ra_e), math.radians(dec_e)
        cosc = math.sin(dec0) * math.sin(dec) + math.cos(dec0) * math.cos(dec) * math.cos(ra - ra0)
        if cosc < 0.5:
            continue
        xi = math.cos(dec) * math.sin(ra - ra0) / cosc  # towards east
        eta = (math.cos(dec0) * math.sin(dec) - math.sin(dec0) * math.cos(dec) * math.cos(ra - ra0)) / cosc
        # Sky parity: east is to the left when north is up.
        x = -xi * cr - eta * sr
        y = -xi * sr + eta * cr
        px = cam.width / 2 + x * scale
        py = cam.height / 2 - y * scale
        if not (6 <= px < cam.width - 7 and 6 <= py < cam.height - 7):
            continue
        peak = 45.0 * 10 ** (-0.4 * (s["mag"] - cam.mag_limit))  # faintest stars ~15x noise
        ix, iy = int(round(px)), int(round(py))
        sub = np.exp(-((xx + ix - px) ** 2 + (yy + iy - py) ** 2) / (2 * cam.psf_sigma_px**2))
        img[iy - 6: iy + 7, ix - 6: ix + 7] += peak * sub
    return np.clip(img, 0, 255).astype(np.uint8)


@lru_cache(maxsize=1)
def _tetra() -> Any:
    try:
        import tetra3  # type: ignore[import-not-found]
    except ImportError as exc:
        raise SolverUnavailable("plate solving needs the optional 'cedar-solve' package") from exc
    import logging

    if not hasattr(np, "math"):
        np.math = math  # type: ignore[attr-defined]  # tetra3 uses np.math, removed in numpy 2
    logging.getLogger("tetra3.Tetra3").setLevel(logging.WARNING)
    return tetra3.Tetra3()


def solver_available() -> bool:
    try:
        _tetra()
        return True
    except SolverUnavailable:
        return False


def solve_image(image: np.ndarray, fov_estimate: float | None = None) -> dict[str, Any]:
    """Detect stars and solve. Returns RA/Dec (deg, J2000), roll, FOV, matches, or ``solved: False``."""
    t3 = _tetra()
    import tetra3  # type: ignore[import-not-found]

    centroids = tetra3.get_centroids_from_image(image.astype(np.float32), max_returned=40)
    if len(centroids) < 4:
        return {"solved": False, "stars_detected": int(len(centroids)), "reason": "too few stars detected"}
    res = t3.solve_from_centroids(centroids, image.shape[:2], fov_estimate=fov_estimate, fov_max_error=5.0 if fov_estimate else None)
    if res.get("RA") is None:
        return {"solved": False, "stars_detected": int(len(centroids)), "reason": "no match"}
    return {
        "solved": True,
        "ra": float(res["RA"]),
        "dec": float(res["Dec"]),
        "roll": float(res["Roll"]),
        "fov": float(res["FOV"]),
        "matches": int(res["Matches"]),
        "rmse_arcsec": float(res["RMSE"]),
        "solve_ms": float(res["T_solve"]),
        "stars_detected": int(len(centroids)),
    }


def pointing_correction(engine: Any, requested: tuple[float, float], measured_radec: tuple[float, float],
                        unix: float) -> dict[str, float]:
    """Alt/Az correction (requested - measured) to add to the mount's pointing model."""
    target = engine.resolve(f"radec:{measured_radec[0] % 360:.6f},{measured_radec[1]:.6f}")
    m_alt, m_az = engine.altaz(target, unix)
    d_alt = requested[0] - float(m_alt)
    d_az = (requested[1] - float(m_az) + 180.0) % 360.0 - 180.0
    sep = math.degrees(math.acos(max(-1.0, min(1.0,
        math.sin(math.radians(requested[0])) * math.sin(math.radians(float(m_alt)))
        + math.cos(math.radians(requested[0])) * math.cos(math.radians(float(m_alt))) * math.cos(math.radians(d_az))))))
    return {"d_alt": d_alt, "d_az": d_az, "measured_alt": float(m_alt), "measured_az": float(m_az), "error_deg": sep}


def simulated_alignment(controller: Any, apply: bool = True, camera: CameraModel | None = None) -> dict[str, Any]:
    """Simulate a camera exposure at the mount's *true* pointing, solve it, and add an alignment point.

    Only possible with the simulator (it knows the true pointing). With real hardware
    the image would come from the payload camera instead.
    """
    st = controller.driver.status()
    if not st.truth:
        raise SolverUnavailable("simulated camera requires the simulated mount")
    engine = controller.engine
    t = controller._t
    rd = engine.altaz_to_radec(st.truth["alt"], st.truth["az"], t)
    img = render_star_field(engine.catalog, rd["ra_j2000"], rd["dec_j2000"], roll_deg=17.0, camera=camera)
    sol = solve_image(img, fov_estimate=(camera or CameraModel()).fov_deg)
    out: dict[str, Any] = {"solution": sol, "true_radec": {"ra": rd["ra_j2000"], "dec": rd["dec_j2000"]}}
    if not sol["solved"]:
        return out
    snap = controller.snapshot(with_radec=False)
    requested = (snap["target"]["alt"], snap["target"]["az"]) if snap["target"] else (snap["position"]["alt"], snap["position"]["az"])
    corr = pointing_correction(engine, requested, (sol["ra"], sol["dec"]), t)
    out["correction"] = corr
    out["solve_vs_truth_arcsec"] = 3600 * math.degrees(math.acos(min(1.0,
        math.sin(math.radians(sol["dec"])) * math.sin(math.radians(rd["dec_j2000"]))
        + math.cos(math.radians(sol["dec"])) * math.cos(math.radians(rd["dec_j2000"])) * math.cos(math.radians(sol["ra"] - rd["ra_j2000"])))))
    if apply:
        # The solution says where the payload really points: record it as an alignment point.
        label = f"plate solve near {snap['target']['name']}" if snap["target"] else "plate solve"
        res = controller.add_alignment_point(corr["measured_alt"], corr["measured_az"], label, "plate_solve")
        out["applied"] = True
        out["model"] = res["model"]
    return out
