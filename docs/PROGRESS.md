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
  push and API writes). All work is committed locally on the branch. See NEXT_TASKS #0.
