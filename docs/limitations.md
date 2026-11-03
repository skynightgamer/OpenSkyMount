# Known limitations

- **No hardware validation.** All motion behaviour comes from the simulator. Real motors, gearing, sensors and power
  will differ.
- **Field rotation**: an alt-az mount does not remove field rotation. The UI shows the rate (arcsec/s) at the
  pointing direction. Long exposures need short sub-exposures with stacking or a field de-rotator. As a rough rule,
  the maximum exposure before 1 px of rotation at the frame corner shrinks sharply near the zenith and towards the
  meridian.
- **Zenith keyhole**: close to the zenith the required azimuth rate exceeds the mount's limits. Tracking lags (flag
  `rate_limited`) and recovers after transit.
- **Pointing model**: fits axis zero offsets (1+ stars) and base tilt (3+ stars). Cone error and axis
  non-perpendicularity are not modelled, so heavily misbuilt mounts keep residual errors.
- **Catalog**: stars to magnitude 8 (Hipparcos-2 astrometry, proper motion and parallax; 17 stars without a
  Hipparcos-2 solution keep a fixed J2000 position). There are no deep-sky objects beyond the Messier list, and no
  comets, asteroids or satellites.
- **Observing planner**: ranks with a simple documented score (time above the altitude limit, peak altitude,
  brightness, object type). It ignores light pollution, surface brightness and the Moon's glare.
- **Ephemeris**: DE421 covers 1900–2053.
- **Moon brightness** is a constant (−12.7). The phase is computed and shown separately.
- **Sky map** renders J2000 star vectors with the true-of-date horizon matrix, which leaves about 20″ of aberration
  unaccounted for (display only; GoTo uses full apparent positions).
- **Security**: an optional shared password over plain HTTP (no TLS). Run only on trusted networks.
- **ASCOM Alpaca**: no `MoveAxis`, pulse guiding, park or find-home yet; `SlewToCoordinates`/`Sync` do not require
  `Tracking=true` first (documented deviation). Tested with alpyca only, not with N.I.N.A./Stellarium or ConformU.
  Discovery is IPv4 only. With simulated time, Alpaca clients see the simulated clock.
- **Plate solving**: experimental and validated only on synthetic images. It needs `cedar-solve`, installed with
  `--no-deps`, plus a numpy-2 compatibility shim.
- **Simulator idealisations**: homing and encoder re-referencing know which side of the backlash slack the gears
  sit on; the backlash compensation value equals the true simulated backlash.
