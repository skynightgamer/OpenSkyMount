# Constellation artwork

The pictures in this folder (`<IAU abbreviation>.png`) are the constellation illustrations of the **Stellarium**
project's "modern" sky culture, fetched by `scripts/build_figures.py` from
<https://github.com/Stellarium/stellarium> (commit `aa2b7127eb991b02edfc7c9208622057271feb1d`,
`skycultures/modern/illustrations/`). The illustrations were created by **Johan Meuris**:
<https://johanmeuris.eu/work/stellarium-constellation-art/>.

- **Licence of the pictures: Free Art License** (copyleft: keep this notice and the attribution; copies and works
  derived from the pictures stay under the Free Art License). Stellarium states this in
  `skycultures/modern/description.md` ("Illustrations: Free Art License"). The licence text is at
  <https://artlibre.org/licence/lal/en/>.
- The anchor stars (three per picture, in `frontend/src/sky/figures.json`) come from the same sky culture's
  `index.json`, licensed CC BY-SA 4.0 ("Text and data").
- The OpenSkyMount source code is MIT-licensed and does not include any Stellarium code. Only these pictures and the
  anchor data are taken from Stellarium; they are shipped as separate files, not compiled into the program.

The "Figures" layer is switchable in the sky map; turning it off, or deleting this folder and `figures.json`'s
entries, removes the artwork without affecting the rest of the program.
