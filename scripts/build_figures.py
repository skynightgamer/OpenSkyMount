#!/usr/bin/env python3
"""Fetch the constellation artwork used by the sky map's "Figures" layer.

Source: the Stellarium project's modern sky culture (https://github.com/Stellarium/stellarium,
skycultures/modern). The illustrations are under the Free Art License (see frontend/public/figures/NOTICE.md); the
anchor data (three stars per picture) is CC BY-SA 4.0. The commit is pinned so the result is reproducible.

Writes:
  frontend/public/figures/<IAU id>.png   the pictures, lazily loaded by the browser
  frontend/src/sky/figures.json          {id: {w, h, anchors: [[px, py, hip, ra, dec], ...], english}} for the renderer
                                         (ra/dec = the anchor star's J2000 position from backend/.../catalog.json)

Usage: python3 scripts/build_figures.py   (needs network; stdlib only)
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

COMMIT = "aa2b7127eb991b02edfc7c9208622057271feb1d"
RAW = f"https://raw.githubusercontent.com/Stellarium/stellarium/{COMMIT}/skycultures/modern/"
ROOT = Path(__file__).resolve().parent.parent
PNG_DIR = ROOT / "frontend" / "public" / "figures"
JSON_OUT = ROOT / "frontend" / "src" / "sky" / "figures.json"
CATALOG = ROOT / "backend" / "openskymount" / "data" / "catalog.json"


def get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as r:  # noqa: S310 - fixed https URL on raw.githubusercontent.com
        return r.read()


def main() -> int:
    index = json.loads(get(RAW + "index.json"))
    # Baked anchor positions: two anchors are fainter than the sky map's bright star tier, so the browser could not
    # look them up; at 35 years of proper motion the shift is far below one picture pixel.
    stars = {s[0]: (round(s[1], 5), round(s[2], 5)) for s in json.loads(CATALOG.read_text())["stars"]}
    PNG_DIR.mkdir(parents=True, exist_ok=True)
    out: dict[str, dict] = {}
    for c in index["constellations"]:
        img = c.get("image")
        if not img:
            continue
        cid = c["id"].split()[-1]  # "CON modern Aql" -> "Aql"
        if len(img["anchors"]) != 3:
            print(f"skip {cid}: {len(img['anchors'])} anchors", file=sys.stderr)
            continue
        png = get(RAW + img["file"])
        (PNG_DIR / f"{cid}.png").write_bytes(png)
        out[cid] = {
            "w": img["size"][0],
            "h": img["size"][1],
            "anchors": [[a["pos"][0], a["pos"][1], a["hip"], *stars[a["hip"]]] for a in img["anchors"]],
            "english": c.get("common_name", {}).get("english", ""),
        }
        print(f"{cid:4} {img['file']:34} {len(png) // 1024:4d} KB")
    body = ",\n".join(f" {json.dumps(k)}:{json.dumps(v, separators=(',', ':'))}" for k, v in sorted(out.items()))
    JSON_OUT.write_text("{\n" + body + "\n}\n")
    print(f"{len(out)} figures -> {PNG_DIR.relative_to(ROOT)}, {JSON_OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
