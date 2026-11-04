#!/usr/bin/env python3
"""Build the Milky Way brightness map used by the sky map (frontend/src/sky/milkyway.png).

Source: `data/mw.json` from the d3-celestial npm package (BSD-3-Clause, see
backend/openskymount/data/LICENSE.d3-celestial), which contains the Milky Way Outline Catalog by Jose R. Vieira as
five nested brightness levels (GeoJSON, J2000, longitude = RA in -180..180 degrees).

Output: 8-bit grayscale equirectangular image, 720 x 360 (0.5 degree cells). Column i covers RA (i + 0.5) / 2 degrees,
row j covers Dec 90 - (j + 0.5) / 2 degrees. Value = (number of outline levels containing the cell) / 5 * 255, smoothed
with a Gaussian of about 1 degree so the band edges are soft.

Usage (needs numpy and Pillow; not a runtime dependency):
    npm pack d3-celestial && tar xzf d3-celestial-*.tgz
    python3 scripts/build_milkyway.py package/data/mw.json frontend/src/sky/milkyway.png
"""

from __future__ import annotations

import json
import sys

import numpy as np
from PIL import Image, ImageFilter

SUPER = 4  # cells per degree while rasterising (downsampled to 2 per degree)


def level_mask(rings: list[list[list[float]]], ra: np.ndarray, dec: np.ndarray) -> np.ndarray:
    """Even-odd inside test on the sphere: count ring crossings of the meridian arc from each cell up to the north
    celestial pole (which lies outside every Milky Way outline)."""
    lon0, lat0, lon1, lat1 = [], [], [], []
    for ring in rings:
        r = np.asarray(ring, dtype=float)
        a, b = r[:-1], r[1:]
        lo_a = np.mod(a[:, 0], 360.0)
        lo_b = np.mod(b[:, 0], 360.0)
        # Unwrap edges that cross RA 0/360 so each edge spans less than 180 degrees.
        d = lo_b - lo_a
        lo_b = np.where(d > 180, lo_b - 360, np.where(d < -180, lo_b + 360, lo_b))
        lon0.append(lo_a)
        lat0.append(a[:, 1])
        lon1.append(lo_b)
        lat1.append(b[:, 1])
    x0, y0, x1, y1 = (np.concatenate(v) for v in (lon0, lat0, lon1, lat1))
    inside = np.zeros((dec.size, ra.size), dtype=bool)
    for i, x in enumerate(ra):
        for shift in (0.0, 360.0, -360.0):
            xs = x + shift
            sel = ((x0 <= xs) & (xs < x1)) | ((x1 <= xs) & (xs < x0))
            if not sel.any():
                continue
            t = (xs - x0[sel]) / (x1[sel] - x0[sel])
            ycross = y0[sel] + t * (y1[sel] - y0[sel])
            # crossings above each cell
            above = (ycross[None, :] > dec[:, None]).sum(axis=1)
            inside[:, i] ^= (above % 2).astype(bool)
    return inside


def main(src: str, dst: str) -> None:
    with open(src, encoding="utf-8") as fh:
        feats = json.load(fh)["features"]
    n_ra, n_dec = 360 * SUPER, 180 * SUPER
    ra = (np.arange(n_ra) + 0.5) / SUPER
    dec = 90 - (np.arange(n_dec) + 0.5) / SUPER
    total = np.zeros((n_dec, n_ra), dtype=float)
    for f in feats:
        mask = np.zeros_like(total, dtype=bool)
        for poly in f["geometry"]["coordinates"]:
            mask ^= level_mask(poly, ra, dec)
        total += mask
        print(f"{f['id']}: {mask.mean() * 100:.1f}% of the map", file=sys.stderr)
    img = Image.fromarray(np.clip(total / len(feats) * 255, 0, 255).astype(np.uint8), mode="L")
    img = img.filter(ImageFilter.GaussianBlur(radius=SUPER * 0.8))
    img = img.resize((720, 360), Image.Resampling.LANCZOS)
    img.save(dst, optimize=True)
    print(f"wrote {dst}", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
