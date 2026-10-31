#!/usr/bin/env python3
"""Build the compact OpenSkyMount catalog from d3-celestial GeoJSON data.

Source: https://github.com/ofrohn/d3-celestial (BSD-3-Clause, (c) 2015 Olaf Frohn).
Star positions there are Hipparcos-derived J2000 coordinates.

Usage:
    python scripts/build_catalog.py <dir-with-d3-celestial-data> <output.json>

The generated file is committed to the repository so normal operation needs
no network access.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def ra_from_lon(lon: float) -> float:
    """d3-celestial stores RA as longitude in [-180, 180]; convert to [0, 360)."""
    return round(lon % 360.0, 5)


def clean(text: str) -> str:
    return text.replace(" ", " ").replace(" ", " ").strip()


def main(src: Path, out: Path) -> None:
    stars_geo = json.loads((src / "stars.6.json").read_text())
    names = json.loads((src / "starnames.json").read_text())
    const_geo = json.loads((src / "constellations.json").read_text())
    lines_geo = json.loads((src / "constellations.lines.json").read_text())
    messier_geo = json.loads((src / "messier.json").read_text())

    stars = []
    for f in stars_geo["features"]:
        hip = int(f["id"])
        lon, dec = f["geometry"]["coordinates"]
        p = f["properties"]
        info = names.get(str(hip), {})
        bayer = clean(info.get("bayer", ""))
        flam = clean(info.get("flam", ""))
        con = info.get("c", "")
        desig = ""
        if bayer and con:
            desig = f"{bayer} {con}"
        elif flam and con:
            desig = f"{flam} {con}"
        try:
            bv = float(p.get("bv", "") or 0.6)
        except ValueError:
            bv = 0.6
        stars.append(
            {
                "hip": hip,
                "ra": ra_from_lon(lon),
                "dec": round(dec, 5),
                "mag": p["mag"],
                "bv": round(bv, 2),
                "name": clean(info.get("name", "")),
                "desig": desig,
                "con": con,
            }
        )
    stars.sort(key=lambda s: s["mag"])

    constellations = []
    line_map = {f["id"]: f["geometry"]["coordinates"] for f in lines_geo["features"]}
    for f in const_geo["features"]:
        cid = f["id"]
        props = f["properties"]
        lon, dec = f["geometry"]["coordinates"]
        polylines = [
            [[ra_from_lon(pt[0]), round(pt[1], 4)] for pt in seg] for seg in line_map.get(cid, [])
        ]
        constellations.append(
            {
                "id": cid,
                "name": props["name"],
                "label_ra": ra_from_lon(lon),
                "label_dec": round(dec, 4),
                "lines": polylines,
            }
        )

    type_names = {
        "gc": "Globular cluster",
        "oc": "Open cluster",
        "pn": "Planetary nebula",
        "bn": "Bright nebula",
        "en": "Emission nebula",
        "rn": "Reflection nebula",
        "snr": "Supernova remnant",
        "sg": "Spiral galaxy",
        "eg": "Elliptical galaxy",
        "ig": "Irregular galaxy",
        "lg": "Lenticular galaxy",
        "s0": "Lenticular galaxy",
        "ds": "Double star",
        "as": "Asterism",
        "pos": "Asterism",
        "sd": "Star cloud",
        "dn": "Dark nebula",
        "s": "Spiral galaxy",
        "e": "Elliptical galaxy",
        "i": "Irregular galaxy",
        "sfr": "Star-forming nebula",
    }
    # Common names that read better than the source's short forms.
    rename = {"M31": "Andromeda Galaxy", "M33": "Triangulum Galaxy"}
    dsos = []
    for f in messier_geo["features"]:
        p = f["properties"]
        lon, dec = f["geometry"]["coordinates"]
        dsos.append(
            {
                "id": f["id"],
                "name": rename.get(f["id"], clean(p.get("alt", ""))),
                "desig": clean(p.get("desig", "")),
                "type": type_names.get(p.get("type", ""), p.get("type", "")),
                "mag": p.get("mag"),
                "ra": ra_from_lon(lon),
                "dec": round(dec, 5),
            }
        )

    data = {
        "source": "d3-celestial by Olaf Frohn (BSD-3-Clause), Hipparcos-derived J2000 positions",
        "epoch": "J2000",
        "stars": stars,
        "constellations": constellations,
        "dsos": dsos,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False))
    print(f"wrote {out}: {len(stars)} stars, {len(constellations)} constellations, {len(dsos)} DSOs")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(Path(sys.argv[1]), Path(sys.argv[2]))
