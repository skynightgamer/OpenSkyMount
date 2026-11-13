# Test results

2026-11-13 UI update: `scripts/test.sh` passed (149 backend tests, 8 optional skips; 59 frontend tests), and the production build passed. All four `scripts/e2e.sh` scenarios passed with locally installed Chromium 1140, including the updated Best tonight flow, Night mode, About, welcome dismissal and guide dragging/keyboard movement. Playwright 1.61 was pointed at the locally installed Chromium using a temporary preload. No real touch device or screen-reader test was performed. The initial sandboxed test run could not bind localhost sockets; the rerun with socket access passed.

Review rerun on 2026-11-12: `scripts/test.sh` passed (149 backend tests, 8 skipped; 57 frontend tests), and the frontend production build passed. The updated Best tonight browser E2E was not rerun in this review.

Last full run: 2026-11-12 (session 11), macOS (local), Python 3.15, Node 22, Playwright 1.48.2 Chromium (installed
outside the project, passed with `NODE_PATH`), **without** the optional extras (Astropy, cedar-solve; their 8 tests skipped, they
run in CI's 3.13 job). Versions are listed in `backend/requirements.lock.txt` and `frontend/package-lock.json`. The same
checks run in GitHub Actions on every push (`.github/workflows/ci.yml`): backend on Python 3.11 (without extras) and 3.13
(with extras), frontend, and browser E2E.

| Suite | Command | Result |
|---|---|---|
| Lint (ruff) | `ruff check backend/openskymount backend/tests` | pass |
| Types (mypy) | `cd backend && mypy openskymount` | pass (32 files) |
| Backend tests | `cd backend && pytest -q` | **149 passed, 8 skipped** in ~84 s without the optional extras (the OnStep tests run in real time over TCP, ~37 s) |
| Frontend types | `cd frontend && npx tsc -p tsconfig.json` | pass |
| Frontend unit | `cd frontend && npx vitest run` | **57 passed** (constellation figures: anchors land on their stars, each picture sits over its own constellation and none is mirrored, focus, triangle transform, daylight fade; selection reticle: slerp, settle/ripple, glide, reduced motion; scenery: ecliptic vs catalogued solstice positions, seamless skyline, twinkle strength by altitude, camera flights; projection math, sky rendering: Milky Way map vs galactic coordinates, sky colour model, Moon bright limb; Earth-rotation extrapolation, formatting, fixed-width readouts, angle formats, time-zone round trip incl. DST, exposure planner, gamepad mapping and dead-man refresh, telemetry parsing/statistics/CSV) |
| Browser E2E | `node frontend/e2e/smoke.cjs` (headless Chromium, desktop + phone viewport) | **pass** (tonight list, search, exposure planner, display options, GoTo, E-STOP stays in view while the control column scrolls, two-client sync, Sync → alignment, E-STOP, section open state survives a reload). **Stale for the Best tonight popover (D51):** the script was updated for it (popover starts closed, opens, lists, Escape closes) but has not been re-run |
| Browser E2E (all scripts) | `scripts/e2e.sh` (builds the UI, starts four servers, waits for `/api/health`) | **pass** |
| Browser E2E, password mode | `node frontend/e2e/auth.cjs` (server with `OPENSKYMOUNT_PASSWORD`) | **pass** (login screen, E-STOP without login, wrong password, catalog loads after login, reload keeps session, logout) |
| Browser E2E, accounts | `node frontend/e2e/accounts.cjs` (fresh server via 127.0.0.1) | **pass** (welcome screen once per browser; setup guide: take control, keep site, payload, progress survives a reload; create the first admin from the account menu; a second browser must then log in; admin adds an operator and an observer; observer sees "View only · observer", no Take control, E-STOP works; deleting the observer logs that browser out at once; operator changes password, old one refused, new one works; logout) |
| Browser E2E, features | `node frontend/e2e/features.cjs` (fresh server) | **pass** (paste ISS TLE → pass list → session with the pass and Capella at ×60 → ISS tracked (true error ~1″ in the readout, 7.2″ RMS / 34″ max over the pass) → recording stopped with the session → replay statistics; emulated gamepad: no motion without dead man, slew while RB held, stop on release, Back = E-STOP; phone `?view=hand`: take control, press-and-hold pad; manifest, service worker and icons served) |

## Coverage by area

| Area | Tests (file) | What is verified |
|---|---|---|
| Astronomy | `test_astronomy.py` (23) + `test_astropy_crosscheck.py` (7, needs astropy) | Meeus examples 13.b (alt/az of Venus, USNO), 25.b (Sun), 33.a (Venus), 47.a (Moon) within 2–15″ / 0.01°, 23.a (θ Per apparent place with proper motion) within 1.5″; Hipparcos-2 proper motions (61 Cyg A) and browser propagation vs Skyfield < 1″; Astropy space-motion cross-check; field rotation vs parallactic-angle derivative; Astropy cross-check at 3 sites (stars < 5″, planets < 60″); leap seconds; Unix-time round trip; ICRS→horizon matrix vs full computation (< 25″); refraction; inverse alt/az→RA/Dec; Polaris altitude ≈ latitude; southern hemisphere; search and target resolution (incl. genitive designations: alpha Lyrae, 61 Cygni, alpha Centauri → α1 Cen, Boötis); invalid input; **apparent place of date ("JNow") ↔ ICRS**: inverse of Meeus 23.a within 1.5″, Astropy TETE within 0.5″ (forward and inverse), round trip < 0.001″ |
| Motion physics | `test_motion.py` (13) | Velocity/acceleration bounds, no overshoot, trapezoidal slew time, ramp tracking, stop distance, backlash dead band, stall, hard limits, latency, determinism, E-stop latch, encoder stall detection, disconnect safe-stop, homing re-reference, homing failure |
| Controller | `test_controller.py` (30) | GoTo convergence; GoTo to a target on the cable-wrap limit settles (regression); 2 h tracking of Vega, Capella, Jupiter, Moon, M31, Polaris, Deneb within 72″ (true error); steady-state error < 5″ at update spans of 0.05/1/10 s; Polaris azimuth wrap over 12 h; cable-wrap unwind; Sun and near-Sun refusal; solar opt-in (simulator only); below-horizon and invalid targets; payload altitude limit; time jump re-acquire or stop; E-stop precedence; manual dead-man (wall time); soft limits; fault halt and recovery; disconnect/reconnect without resumed motion; plate-solve style correction; homing; determinism; zenith pass recovery |
| Accounts | `test_accounts.py` (18) | Store: scrypt hashes (no plaintext), file mode 0600, user-name/password/role rules, first account must be admin, last admin cannot be demoted or deleted, reload of on-disk changes, damaged file refused at start and ignored while running; CLI add/passwd/delete. HTTP: first admin only from loopback or after a shared-password login, 409 afterwards, sign-in then required everywhere; open WebSockets closed with 4401 after setup; login/logout, throttling, "remember me" cookie; observer: 403 on POST, control refused, E-stop over WS and REST works; operator control and display name in control info; admin create/list/role change (applies to live logins)/password reset (ends logins)/delete; deleted user's WebSocket closed; demoted controller loses control; own profile, password change (wrong/short refused, other logins end, this device gets a new session), end one login by id, log out everywhere; password changed with the CLI ends logins; HTTP Basic with account credentials for Alpaca (cached, observer 403 on PUT, deleted account refused) |
| Password mode | `test_auth.py` (7) | Open without password; 401 / WS 4401 without login; cookie flags; logout revokes; Bearer header; login throttling (429); E-stop without login; password length |
| Server | `test_server.py` (6) | REST endpoints; control authority (observer refused, lease, forced takeover); E-stop from an observer; replay rejection; bad JSON and arguments; **two clients see the same GoTo and tracking state**; controller disconnect stops manual motion; config persistence; simulated alignment command |
| Pointing model | `test_pointing.py` (6) | Mapping round trip; exact recovery of offsets + tilt from ≥ 3 points; offsets-only for 1–2 points; residual reporting; **simulated misaligned and tilted mount: 1-star leaves > 0.1° elsewhere, 3-star < 0.02° at all check stars**; Sync replace/remove |
| Observing planner | `test_tonight.py` (5) | Darkness window boundaries (Sun at −12°), already-dark start, polar summer, content and ordering, fast method vs full apparent position (< 0.05°), culmination altitude, type filter |
| ASCOM Alpaca | `test_alpaca.py` (11) | Management API; **connection details for Settings (`/api/alpaca`: loopback vs all interfaces, port, discovery, login, client list, disabled)**; response envelope (transaction ids, case-insensitive parameter names); HTTP 400/404 and ASCOM error numbers (NotConnected, NotImplemented, InvalidValue, ValueNotSet, InvalidOperation, ActionNotImplemented); per-client connection; reported position = controller pointing; **GoTo by JNow coordinates ends tracking Capella within 10″ of its true position**; Sync adds an alignment point; Tracking on/off and refusal below the altitude limit; AbortSlew (incl. restoring tracking); **control authority** (refused while the browser holds control, AbortSlew still accepted, takes free control, browser can take it back); **solar exclusion**; site validation; idle-client purge; HTTP Basic in password mode; `--no-alpaca`; UDP discovery reply; **end-to-end with alpyca over a real HTTP server**. Encoder noise is switched off in these tests (it is tested in `test_motion.py`) so 10″ tolerances check the coordinates, not the noise model |
| Hardware path | `test_protocol.py` (6) | OSM/1 parsing and round trip; fake-firmware replies; GoTo, manual and E-stop over real TCP; strict solar policy on the hardware path; firmware watchdog; link-loss detection and reconnect |
| OnStepX / LX200 adapter | `test_onstep.py` (12) | LX200 angle format/parse; fake OnStep replies (ACK, GoTo codes); **GoTo, manual slew (direction) and E-stop through the controller over TCP**; tracking a moving setpoint with rate commands only, worst error < 15″, no GoTo; multi-turn azimuth through the 0/360 seam with GoTo legs; sync offset; reversed azimuth wiring → `direction_mismatch` and stop; host watchdog stops motion; link loss and reconnect; equatorial mode refused, low-precision replies switched with `:U#`; firmware GoTo refusal → `limit_alt`; firmware turning the other way round → `goto_rejected`; strict solar policy. Against `fake_onstep.py` only, **not real OnStepX** |
| Orbital elements | `test_orbits.py` (7) | TLE parsing, checksums, 3LE names, epoch; MPC comet and MPCORB parsing, packed epochs; store persistence, search, removal; **ISS alt/az vs an independent sgp4 → GMST (IAU 1982) → WGS84 ENU chain < 30″** over 3+ passes, rise/set at 10°; **comet Encke vs Meeus 33.a** (RA within 0.5″, Δ within 2e-5 AU) and vs an independent Kepler propagation (< 1″); asteroid (MPCORB) vs independent Kepler (< 1″) and Ceres-range magnitude; **ISS pass tracking stress test**: GoTo at rise, worst true error < 30″ at > 1.5°/s, no cable unwind, ends below the limit |
| Observing session | `test_sequencer.py` (4) | Queue order, durations (±1.5 s), "until next start", start times, skip of a below-horizon target with reason, completion, persistence and reset; Stop/E-STOP/fault pause without restarting motion and resume; Sun refused; satellite pass item ends when the ISS sets |
| Server features | `test_server_features.py` (2) | Orbit upload (text and JSON), bad TLE 400, list/age, search, object info, passes, sky-map bodies, persistence, removal (409 when missing); queue commands need control; session with recording over the real loop; log list/download/format/path traversal/delete |
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
| OnStepX / LX200 adapter | Experimental: verified against the fake OnStep endpoint only; **real firmware blocked by missing hardware** (checklist in docs/hardware.md) |
| ASCOM Alpaca Telescope device | Implemented and verified with automated tests and the alpyca client; **not yet tested with N.I.N.A., Stellarium or ConformU** |
| Observing sessions, satellites (TLE), comets/asteroids (MPC), telemetry replay | Implemented and verified (automated and browser E2E; simulation only; element sets supplied by the user) |
| Gamepad control, hand-controller layout, PWA | Implemented; verified with an emulated Gamepad API and a phone viewport in headless Chromium. **Not tried with a real gamepad or installed on a real phone** |
| INDI exposure, cone/non-perpendicularity terms | Planned |

## Known test caveats
- Astropy cross-checks use Astropy's bundled IERS data with auto-download disabled.
- Synthetic plate-solve images come from the same Hipparcos-derived catalog as the solver database, so real-sky
  robustness (optics distortion, clouds, light pollution) is not tested.
- The E2E test depends on the date set inside the test (2026-11-01 02:00) for Jupiter's visibility; the features
  E2E uses the Skyfield-documentation ISS TLE and a pass on 2014-01-21.
- The axis control-law fix (D37) changed the simulated servo; all existing motion/controller tolerances still pass.
