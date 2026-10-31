# Known limitations

- **No hardware validation.** All motion behaviour comes from the simulator. Real motors, gearing, sensors and power
  will differ.
- **Field rotation**: an alt-az mount does not remove field rotation. The UI shows the rate (arcsec/s) at the
  pointing direction. Long exposures need short sub-exposures with stacking or a field de-rotator. As a rough rule,
  the maximum exposure before 1 px of rotation at the frame corner shrinks sharply near the zenith and towards the
  meridian.
- **Zenith keyhole**: close to the zenith the required azimuth rate exceeds the mount's limits. Tracking lags (flag
  `rate_limited`) and recovers after transit.
- **Pointing model**: only a single offset (Sync or plate solve). A multi-star model (tilt, cone, non-perpendicularity)
  is not implemented. Base tilt is therefore only corrected locally.
- **Catalog**: stars to magnitude 6 without proper motion (J2000 positions; errors grow by a few arcseconds per
  decade for high-proper-motion stars). There are no deep-sky objects beyond the Messier list, and no comets,
  asteroids or satellites.
- **Ephemeris**: DE421 covers 1900–2053.
- **Moon brightness** is a constant (−12.7). The phase is computed and shown separately.
- **Sky map** renders J2000 star vectors with the true-of-date horizon matrix, which leaves about 20″ of aberration
  unaccounted for (display only; GoTo uses full apparent positions).
- **Security**: no authentication. Run only on trusted networks.
- **Plate solving**: experimental and validated only on synthetic images. It needs `cedar-solve`, installed with
  `--no-deps`, plus a numpy-2 compatibility shim.
- **Simulator idealisations**: homing and encoder re-referencing know which side of the backlash slack the gears
  sit on; the backlash compensation value equals the true simulated backlash.
