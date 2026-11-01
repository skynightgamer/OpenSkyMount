#!/usr/bin/env python3
"""Build the compact OpenSkyMount catalog.

Sources:
* d3-celestial (BSD-3-Clause, (c) 2015 Olaf Frohn): star list to V = 8 with HIP ids, V magnitudes,
  B-V colours and names; constellation lines; Messier objects.
  https://github.com/ofrohn/d3-celestial
* Hipparcos, the New Reduction (van Leeuwen 2007, ESA, CDS catalogue I/311, file hip2.dat):
  astrometry (ICRS position at epoch J1991.25, proper motion, parallax). Public ESA data; it can be
  obtained from CDS or from the MIT-licensed PyPI package ``hipparcos-catalog``.

Usage:
    python scripts/build_catalog.py <dir-with-d3-celestial-data> <hip2.dat> <output.json>

The generated file is committed to the repository so normal operation needs
no network access.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path


def ra_from_lon(lon: float) -> float:
    """d3-celestial stores RA as longitude in [-180, 180]; convert to [0, 360)."""
    return round(lon % 360.0, 5)


def clean(text: str) -> str:
    return text.replace(" ", " ").replace(" ", " ").strip()


HIP_EPOCH_JD = 2448349.0625  # J1991.25 (TT)


def read_hip2(path: Path) -> dict[int, tuple[float, float, float, float, float]]:
    """HIP -> (ra_deg, dec_deg, pmra_mas_yr (mu_alpha*), pmdec_mas_yr, parallax_mas), epoch J1991.25."""
    out = {}
    with path.open() as fh:
        for line in fh:
            f = line.split()
            if len(f) < 9:
                continue
            ra = math.degrees(float(f[4]))
            dec = math.degrees(float(f[5]))
            out[int(f[0])] = (ra, dec, float(f[7]), float(f[8]), float(f[6]))
    return out


def main(src: Path, hip2: Path, out: Path) -> None:
    stars_geo = json.loads((src / "stars.8.json").read_text())
    names = json.loads((src / "starnames.json").read_text())
    const_geo = json.loads((src / "constellations.json").read_text())
    lines_geo = json.loads((src / "constellations.lines.json").read_text())
    messier_geo = json.loads((src / "messier.json").read_text())
    astrometry = read_hip2(hip2)

    stars = []
    missing = 0
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
        if hip in astrometry:
            ra_e, dec_e, pmra, pmdec, plx = astrometry[hip]
        else:
            # No Hipparcos-2 solution: keep the d3-celestial J2000 position without motion.
            missing += 1
            ra_e, dec_e, pmra, pmdec, plx = ra_from_lon(lon), dec, 0.0, 0.0, 0.0
        # [hip, ra, dec, pmra, pmdec, plx, mag, bv, name, desig, con]
        stars.append([
            hip, round(ra_e % 360.0, 7), round(dec_e, 7), round(pmra, 2), round(pmdec, 2), round(plx, 2),
            p["mag"], round(bv, 2), clean(info.get("name", "")), desig, con,
        ])
    stars.sort(key=lambda s: s[6])

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
        "source": "Stars: Hipparcos New Reduction (ESA, I/311) astrometry + d3-celestial (BSD-3-Clause, "
        "Olaf Frohn) magnitudes, colours and names; constellations and Messier list from d3-celestial",
        "star_epoch_jd": HIP_EPOCH_JD,
        "star_fields": ["hip", "ra", "dec", "pmra", "pmdec", "plx", "mag", "bv", "name", "desig", "con"],
        "stars": stars,
        "constellations": constellations,
        "dsos": dsos,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False))
    print(f"wrote {out}: {len(stars)} stars ({missing} without Hipparcos-2 astrometry), "
          f"{len(constellations)} constellations, {len(dsos)} DSOs")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
