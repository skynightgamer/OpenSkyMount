# Third-party notices

| Component | Use | License |
|---|---|---|
| d3-celestial data (Olaf Frohn) | Star positions/magnitudes/names, constellation lines, Messier list → `backend/openskymount/data/catalog.json` (converted by `scripts/build_catalog.py`) | BSD-3-Clause, full text in `backend/openskymount/data/LICENSE.d3-celestial` |
| Hipparcos, the New Reduction (van Leeuwen 2007, ESA; CDS I/311), taken from the MIT-licensed PyPI package `hipparcos-catalog` | Star astrometry: positions, proper motions, parallaxes (merged into `catalog.json`) | Public ESA scientific data |
| JPL DE421 ephemeris (via `skyfield-data`) | Planet/Sun/Moon positions | Public domain (NASA/JPL); skyfield-data is MIT |
| Skyfield, jplephem, sgp4 | Astronomy computations | MIT |
| FastAPI, Starlette, Uvicorn, Pydantic | Web server | MIT / BSD-3-Clause |
| NumPy | Numerics | BSD-3-Clause |
| React, React-DOM | UI | MIT |
| three.js | 3D view | MIT |
| Barlow, Barlow Condensed, IBM Plex Mono (via @fontsource, bundled into the UI build) | Interface typefaces, served locally (no font CDN) | SIL Open Font License 1.1 |
| Vite, TypeScript, Vitest | Build/test tooling (not shipped) | MIT / Apache-2.0 |
| cedar-solve (fork of ESA tetra3) — *optional, not bundled* | Plate-solving proof of concept | Apache-2.0 (tetra3 derivative of MIT-licensed Tetra) |
| Astropy — *optional, tests only* | Independent cross-check | BSD-3-Clause |

All licenses are compatible with distributing OpenSkyMount under MIT. No code was copied from
GPL-licensed projects (Stellarium, OnStepX, INDI, astrometry.net were evaluated but not incorporated).
