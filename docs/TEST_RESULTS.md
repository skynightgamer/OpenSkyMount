# Test results

Last full run: 2026-10-31, Linux cloud container, Python 3.13.18, Node 22.23. Versions are listed in
`backend/requirements.lock.txt` and `frontend/package-lock.json`.

| Suite | Command | Result |
|---|---|---|
| Lint (ruff) | `ruff check backend/openskymount backend/tests` | pass |
| Types (mypy) | `cd backend && mypy openskymount` | pass (20 files) |
| Backend tests | `cd backend && pytest -q` | **77 passed** in ~41 s with optional extras installed; clean `pip install -e backend[dev]` without extras: 67 passed, 8 skipped (astropy module + solver tests) |
| Frontend types | `cd frontend && npx tsc -p tsconfig.json` | pass |
| Frontend unit | `cd frontend && npx vitest run` | **6 passed** (projection math, Earth-rotation extrapolation, formatting) |
| Browser E2E | `node frontend/e2e/smoke.cjs` (headless Chromium, desktop + phone viewport) | **pass** |

## Coverage by area

| Area | Tests (file) | What is verified |
|---|---|---|
| Astronomy | `test_astronomy.py` (14) + `test_astropy_crosscheck.py` (3, needs astropy) | Meeus examples 13.b (alt/az of Venus, USNO), 25.b (Sun), 33.a (Venus), 47.a (Moon) within 2–15″ / 0.01°; Astropy cross-check at 3 sites (stars < 5″, planets < 60″); leap seconds; Unix-time round trip; ICRS→horizon matrix vs full computation (< 25″); refraction; inverse alt/az→RA/Dec; Polaris altitude ≈ latitude; southern hemisphere; search and target resolution; invalid input |
| Motion physics | `test_motion.py` (13) | Velocity/acceleration bounds, no overshoot, trapezoidal slew time, ramp tracking, stop distance, backlash dead band, stall, hard limits, latency, determinism, E-stop latch, encoder stall detection, disconnect safe-stop, homing re-reference, homing failure |
| Controller | `test_controller.py` (29) | GoTo convergence; 2 h tracking of Vega, Capella, Jupiter, Moon, M31, Polaris, Deneb within 72″ (true error); steady-state error < 5″ at update spans of 0.05/1/10 s; Polaris azimuth wrap over 12 h; cable-wrap unwind; Sun and near-Sun refusal; solar opt-in (simulator only); below-horizon and invalid targets; payload altitude limit; time jump re-acquire or stop; E-stop precedence; manual dead-man (wall time); soft limits; fault halt and recovery; disconnect/reconnect without resumed motion; plate-solve style correction; homing; determinism; zenith pass recovery |
| Server | `test_server.py` (6) | REST endpoints; control authority (observer refused, lease, forced takeover); E-stop from an observer; replay rejection; bad JSON and arguments; **two clients see the same GoTo and tracking state**; controller disconnect stops manual motion; config persistence; simulated alignment command |
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
| Hardware E-stop, current limits, real sensors | Planned / blocked by missing hardware |
| LX200/OnStepX adapter, ASCOM Alpaca/INDI exposure, multi-star pointing model | Planned |

## Known test caveats
- Astropy cross-checks use Astropy's bundled IERS data with auto-download disabled.
- Synthetic plate-solve images come from the same Hipparcos-derived catalog as the solver database, so real-sky
  robustness (optics distortion, clouds, light pollution) is not tested.
- The E2E test depends on the date set inside the test (2026-11-01 02:00) for Jupiter's visibility.
