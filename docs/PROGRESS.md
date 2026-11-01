# Progress

_Last updated: 2026-10-31 (first autonomous session)._ Branch: `claude/gallant-turing-py2w4q`.

## Milestones

| Milestone | Status | Notes |
|---|---|---|
| M1 Interactive sky map | **Done, verified** | Stars ≤ mag 6, constellations, Messier, Sun/Moon/planets, grids, horizon, search, object info with rise/transit/set, manual time, responsive layout |
| M2 Virtual motorized mount | **Done, verified (sim)** | 3D model with 5 payload shapes; physics with limits, backlash, latency, noise, faults; telemetry from the real simulation state |
| M3 Automatic tracking | **Done, verified (sim)** | Feed-forward tracking, wrap/unwind, keyhole handling, rising/setting, time jumps, solar-system objects |
| M4 Remote control | **Done, verified** | WebSocket sync, control authority, replay protection, E-stop priority, reconnect handling; two-browser E2E |
| M5 Hardware architecture | **Done for software; hardware blocked** | Driver ABC, OSM/1 protocol, fake firmware, protocol driver, integration tests, BOM and roadmap, safety design |
| M6 Plate solving | **Experimental PoC done** | Synthetic frames → tetra3 solve → alignment correction loop (API, UI, tests) |

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
