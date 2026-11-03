# Test results

Last full run: 2026-11-03, Linux cloud container, Python 3.13.18, Node 22.23, with the optional extras (Astropy,
cedar-solve) and alpyca installed. Versions are listed in `backend/requirements.lock.txt` and
`frontend/package-lock.json`. The same checks run in GitHub Actions on every push (`.github/workflows/ci.yml`):
backend on Python 3.11 (without extras) and 3.13 (with extras), frontend, and browser E2E.

| Suite | Command | Result |
|---|---|---|
| Lint (ruff) | `ruff check backend/openskymount backend/tests` | pass |
| Types (mypy) | `cd backend && mypy openskymount` | pass (24 files) |
| Backend tests | `cd backend && pytest -q` | **119 passed, 0 skipped** in ~75 s with the optional extras; without them (CI, Python 3.11) **104 passed, 8 skipped** (Astropy and solver tests) |
| Frontend types | `cd frontend && npx tsc -p tsconfig.json` | pass |
| Frontend unit | `cd frontend && npx vitest run` | **16 passed** (projection math, Earth-rotation extrapolation, formatting, fixed-width readouts, angle formats, time-zone round trip incl. DST, exposure planner) |
| Browser E2E | `node frontend/e2e/smoke.cjs` (headless Chromium, desktop + phone viewport) | **pass** (tonight list, search, exposure planner, display options, GoTo, two-client sync, Sync → alignment, E-STOP) |
| Browser E2E (both scripts) | `scripts/e2e.sh` (builds the UI, starts both servers, waits for `/api/health`) | **pass** |
| Browser E2E, password mode | `node frontend/e2e/auth.cjs` (server with `OPENSKYMOUNT_PASSWORD`) | **pass** (login screen, E-STOP without login, wrong password, catalog loads after login, reload keeps session, logout) |

## Coverage by area

| Area | Tests (file) | What is verified |
|---|---|---|
| Astronomy | `test_astronomy.py` (23) + `test_astropy_crosscheck.py` (7, needs astropy) | Meeus examples 13.b (alt/az of Venus, USNO), 25.b (Sun), 33.a (Venus), 47.a (Moon) within 2–15″ / 0.01°, 23.a (θ Per apparent place with proper motion) within 1.5″; Hipparcos-2 proper motions (61 Cyg A) and browser propagation vs Skyfield < 1″; Astropy space-motion cross-check; field rotation vs parallactic-angle derivative; Astropy cross-check at 3 sites (stars < 5″, planets < 60″); leap seconds; Unix-time round trip; ICRS→horizon matrix vs full computation (< 25″); refraction; inverse alt/az→RA/Dec; Polaris altitude ≈ latitude; southern hemisphere; search and target resolution; invalid input; **apparent place of date ("JNow") ↔ ICRS**: inverse of Meeus 23.a within 1.5″, Astropy TETE within 0.5″ (forward and inverse), round trip < 0.001″ |
| Motion physics | `test_motion.py` (13) | Velocity/acceleration bounds, no overshoot, trapezoidal slew time, ramp tracking, stop distance, backlash dead band, stall, hard limits, latency, determinism, E-stop latch, encoder stall detection, disconnect safe-stop, homing re-reference, homing failure |
| Controller | `test_controller.py` (30) | GoTo convergence; GoTo to a target on the cable-wrap limit settles (regression); 2 h tracking of Vega, Capella, Jupiter, Moon, M31, Polaris, Deneb within 72″ (true error); steady-state error < 5″ at update spans of 0.05/1/10 s; Polaris azimuth wrap over 12 h; cable-wrap unwind; Sun and near-Sun refusal; solar opt-in (simulator only); below-horizon and invalid targets; payload altitude limit; time jump re-acquire or stop; E-stop precedence; manual dead-man (wall time); soft limits; fault halt and recovery; disconnect/reconnect without resumed motion; plate-solve style correction; homing; determinism; zenith pass recovery |
| Password mode | `test_auth.py` (7) | Open without password; 401 / WS 4401 without login; cookie flags; logout revokes; Bearer header; login throttling (429); E-stop without login; password length |
| Server | `test_server.py` (6) | REST endpoints; control authority (observer refused, lease, forced takeover); E-stop from an observer; replay rejection; bad JSON and arguments; **two clients see the same GoTo and tracking state**; controller disconnect stops manual motion; config persistence; simulated alignment command |
| Pointing model | `test_pointing.py` (6) | Mapping round trip; exact recovery of offsets + tilt from ≥ 3 points; offsets-only for 1–2 points; residual reporting; **simulated misaligned and tilted mount: 1-star leaves > 0.1° elsewhere, 3-star < 0.02° at all check stars**; Sync replace/remove |
| Observing planner | `test_tonight.py` (5) | Darkness window boundaries (Sun at −12°), already-dark start, polar summer, content and ordering, fast method vs full apparent position (< 0.05°), culmination altitude, type filter |
| ASCOM Alpaca | `test_alpaca.py` (10) | Management API; response envelope (transaction ids, case-insensitive parameter names); HTTP 400/404 and ASCOM error numbers (NotConnected, NotImplemented, InvalidValue, ValueNotSet, InvalidOperation, ActionNotImplemented); per-client connection; reported position = controller pointing; **GoTo by JNow coordinates ends tracking Capella within 10″ of its true position**; Sync adds an alignment point; Tracking on/off and refusal below the altitude limit; AbortSlew (incl. restoring tracking); **control authority** (refused while the browser holds control, AbortSlew still accepted, takes free control, browser can take it back); **solar exclusion**; site validation; idle-client purge; HTTP Basic in password mode; `--no-alpaca`; UDP discovery reply; **end-to-end with alpyca over a real HTTP server**. Encoder noise is switched off in these tests (it is tested in `test_motion.py`) so 10″ tolerances check the coordinates, not the noise model |
| Hardware path | `test_protocol.py` (6) | OSM/1 parsing and round trip; fake-firmware replies; GoTo, manual and E-stop over real TCP; strict solar policy on the hardware path; firmware watchdog; link-loss detection and reconnect |
| Plate solving | `test_solve.py` (6) | 5 synthetic fields solved (< 90″, roll < 0.5°, FOV < 0.1°); alignment loop reduces a 0.4–0.7° error to < 0.03° |

## Status classification

| Feature | Status |
|---|---|
| Sky map, search, object info, time control | Implemented and verified (automated and browser E2E) |
| Astronomical accuracy | Implemented and verified against independent references |
| Simulated mount, GoTo, tracking, limits, faults | Implemented and verified (simulation only) |
| Multi-device sync and control authority | Implemented and verified (automated WebSocket tests and two-browser E2E) |
| 3D visualisation | Implemented, partially verified (rendered and screenshot-checked, no pixel assertions) |
| OSM/1 protocol and protocol driver | Implemented and verified against the fake firmware; **real firmware: blocked by missing hardware** |
| Serial transport (pyserial) | Implemented, **not verified** (no device available) |
| Plate solving | Experimental: verified only on synthetic images rendered from the same star catalog |
| Multi-star pointing model, observing planner, exposure planner, mag-8 catalog with proper motion | Implemented and verified (automated; pointing model in simulation only) |
| Hardware E-stop, current limits, real sensors | Planned / blocked by missing hardware |
| ASCOM Alpaca Telescope device | Implemented and verified with automated tests and the alpyca client; **not yet tested with N.I.N.A., Stellarium or ConformU** |
| LX200/OnStepX adapter, INDI exposure, cone/non-perpendicularity terms | Planned |

## Known test caveats
- Astropy cross-checks use Astropy's bundled IERS data with auto-download disabled.
- Synthetic plate-solve images come from the same Hipparcos-derived catalog as the solver database, so real-sky
  robustness (optics distortion, clouds, light pollution) is not tested.
- The E2E test depends on the date set inside the test (2026-11-01 02:00) for Jupiter's visibility.
