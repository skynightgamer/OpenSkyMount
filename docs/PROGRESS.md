# Progress

_Last updated: 2026-11-04 (session 6)._ Main branch: `main` (session 6 work merged from `claude/observing-sessions-target-queue-n9s0w1`).

## Milestones

| Milestone | Status | Notes |
|---|---|---|
| M1 Interactive sky map | **Done, verified** | Stars ≤ mag 6, constellations, Messier, Sun/Moon/planets, grids, horizon, search, object info with rise/transit/set, manual time, responsive layout |
| M2 Virtual motorized mount | **Done, verified (sim)** | 3D model with 5 payload shapes; physics with limits, backlash, latency, noise, faults; telemetry from the real simulation state |
| M3 Automatic tracking | **Done, verified (sim)** | Feed-forward tracking, wrap/unwind, keyhole handling, rising/setting, time jumps, solar-system objects |
| M4 Remote control | **Done, verified** | WebSocket sync, control authority, replay protection, E-stop priority, reconnect handling; two-browser E2E |
| M5 Hardware architecture | **Done for software; hardware blocked** | Driver ABC, OSM/1 protocol, fake firmware, protocol driver, integration tests, BOM and roadmap, safety design |
| M6 Plate solving | **Experimental PoC done** | Synthetic frames → tetra3 solve → alignment correction loop (API, UI, tests) |
| Ecosystem: ASCOM Alpaca | **Done, verified with alpyca** | Telescope device (ITelescopeV4, alt-az), management API, discovery; not yet tested with N.I.N.A./Stellarium/ConformU |
| Observing sessions, satellites/comets, telemetry replay, gamepad, phone layout | **Done, verified (sim)** | Target queue; TLE/MPC elements (user-supplied); fast satellite tracking; JSONL recordings + replay; Gamepad API; hand-controller layout + PWA. See docs/observing.md |
| CI | **Done** | GitHub Actions: backend (3.11, 3.13 + extras), frontend, browser E2E |

## Session log
- **Session 1 (2026-10-31)**: built the complete vertical slice. 77 backend tests, 6 frontend unit tests and the
  browser E2E pass. Found and fixed a tracking-lead bug (16″ → 2″) and an overshoot in the braking law.
  **Pushing to GitHub failed**: the Claude GitHub App has no write access to the repository (HTTP 403 for both git
  push and API writes). Resolved on 2026-11-01: access granted and the branch pushed.
- **Backup**: because the push was refused, a git bundle of this branch is stored as a private artifact owned by the
  repository owner: https://claude.ai/artifact/MtWgURoJfMZABUGyfav7cE (restore steps are on the page; file
  `openskymount-bundle.b64.txt`).
- **Scheduling**: cloud-scheduled continuation sessions were deliberately **not** created. They would start from
  the GitHub repository, which is still empty because of the blocked push, so they could not continue this work
  and would only spend credits. Once access is fixed, use `docs/CONTINUATION_PROMPT.md` in a new session (or
  schedule one).
- **Session 2 (2026-11-01)**: branch pushed after GitHub access was granted; backup artifact deleted. UI redesigned as an instrument panel (bundled fonts, hairline layout, hand-controller keys, status lamps) and a red-light night mode added. E2E and frontend tests pass.
- **Session 3 (2026-11-01)**: multi-star pointing model (zero offsets + base tilt; Sync and plate solving add
  points; Alignment panel); star catalog extended to magnitude 8 with Hipparcos-2 proper motions and parallaxes
  (lazy faint tier in the sky map); exposure planner (field rotation and fixed-tripod limits); observing planner
  ("Best tonight" list, `/api/tonight`). 91 backend tests, 10 frontend unit tests and the extended browser E2E pass.
- **Session 4 (2026-11-01)**: user feedback round. (1) The bottom status bar no longer shifts: every cell is always
  rendered, columns are content-independent grid tracks and values are padded to fixed width (`src/format.ts`);
  measured 1 layout over 6 s of slewing (was 4). (2) The 3D mount model was rebuilt: two-section tripod with
  spreader tray and controller box, lathe-turned azimuth base with a degree setting circle, tapered fork arms,
  stepper motors, a belt-driven altitude axis with its own setting circle, cradles, and more detailed payloads
  (the 80 mm refractor now matches the 0.45 m payload length). Illustrative only, not a CAD model. (3) The
  instrument-panel styling (D19) was replaced by a plain desktop-tool look (D25). Backend tests, 14 frontend unit
  tests and the browser E2E pass.
- **Session 4, continued**: `main` created as the main branch. Implemented the former next tasks 4 and 5:
  display options (sexagesimal or decimal angles, time zone per device) and an optional LAN password
  (`OPENSKYMOUNT_PASSWORD`/`--password`, login screen, cookie sessions, login throttling, E-STOP without login).
  Fixed a bug found while testing it: a failed catalog request was cached, which would have left the sky map
  empty. 88 backend tests (+7), 16 frontend unit tests and both browser E2E scripts pass.
- **Session 5 (2026-11-03)**: former next tasks 1 and 2.
  (1) **GitHub Actions CI** (`.github/workflows/ci.yml`): backend lint/types/tests on Python 3.11 and on 3.13 with
  Astropy and cedar-solve (so cross-checks and solver tests run instead of skipping), frontend type check, unit tests
  and build, and the two browser E2E scripts with Playwright, uploading screenshots and server logs. E2E steps moved
  to `scripts/e2e.sh` (polls `/api/health` instead of sleeping), which `scripts/test.sh --e2e` now calls.
  (2) **ASCOM Alpaca Telescope device** (`server/alpaca.py`, docs/alpaca.md): GoTo/Sync/Tracking/AbortSlew,
  position, site, management API and UDP discovery. Alpaca clients are sessions under the normal control authority
  (they take control only when it is free); all commands go through the controller's safety checks. New
  apparent-place (JNow) ↔ ICRS conversion, checked against Meeus 23.a and Astropy TETE (< 0.5″). Password mode accepts
  HTTP Basic for Alpaca clients.
  (3) **Controller bug found by the Alpaca tests and fixed**: a GoTo whose multi-turn azimuth landed exactly on a
  cable-wrap limit (e.g. az 270 starting from az 90) never finished slewing, because backlash compensation put the
  motor command beyond the limit and the driver clamped it. GoTo now keeps the compensation margin inside the limits
  (regression test `test_goto_to_the_cable_wrap_limit_settles`).
  119 backend tests pass with the optional extras installed (0 skipped); CI: 3.11 job 104 passed / 8 skipped.
  (4) README: new screenshots of the current UI (the old ones still showed the replaced D19 styling and 3D model),
  generated by `frontend/e2e/readme-shots.cjs`; sections on Alpaca use and CI. Merged into `main`.
  **Not done**: ConformU (GitHub release downloads are blocked in the container) and tests with real Alpaca
  programs; `MoveAxis` deliberately left out (dead-man rule, D32).
- **Session 6 (2026-11-03)**: user-requested feature set, branch `claude/observing-sessions-target-queue-n9s0w1`.
  (1) **Observing sessions** (`mount/sequencer.py`): target queue with GoTo → track for a time (or until the next
  start time) → next; start times; skipped/ended-early reasons; pauses on any other motion command and never
  resumes on its own (D34); optional recording during the session; `state/queue.json`.
  (2) **Satellites (TLE) and comets/asteroids (MPC)** (`astro/orbits.py`, engine): user-supplied, checksum-verified
  TLEs; CometEls/MPCORB lines; search, sky map, object info (sunlit, TLE age), pass predictions, magnitudes. Checked
  against an independent SGP4→GMST→ENU chain (< 30″), Meeus 33.a (Encke, RA within 0.5″, Δ within 2e-5 AU) and an
  independent Kepler propagation (< 1″).
  (3) **Fast-tracking stress test** with the ISS found and fixed three problems: setpoints were not led by the command
  latency; the axis model led any moving setpoint by velocity × step (D37, also ~1″ for stars); and a GoTo picked an
  azimuth wrap that forced a cable unwind mid-pass. Now a 70° ISS pass is tracked with < 30″ worst true error
  (~12″ RMS in the browser run) at up to 2.5°/s axis rate, without unwind (D36).
  (4) **Telemetry recording/replay** (`server/recorder.py`, `src/replay.ts`): JSONL logs, REST list/download/delete,
  replay modal with error chart, scrubber, per-target statistics, CSV export.
  (5) **Gamepad** (`src/gamepad.ts`): LB/RB dead man, stick/D-pad rates, B Stop, Back E-STOP, Y/X speed (D39).
  (6) **Phone**: "Hand" layout (big E-STOP, pad, session controls), `?view=hand`, web manifest, icons, service worker
  (secure contexts only) (D40).
  New browser E2E `frontend/e2e/features.cjs` (TLE → pass → session at ×60 → recording → replay; emulated gamepad;
  phone hand layout; PWA files), included in `scripts/e2e.sh`. 118 backend tests pass (8 skipped: optional extras not
  installed this session), 24 frontend unit tests, all three browser E2E scripts.
  **Not done / not verified**: real gamepads (only an emulated Gamepad API object), installing the PWA on a real
  phone, real TLE/MPC files from CelesTrak/MPC (blocked from the container; tested with documented elements).

