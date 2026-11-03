# Architecture

## Overview

```
 Browser clients (phone / tablet / laptop)           Python process (one per mount, runs on a laptop / Raspberry Pi)
 ┌────────────────────────────┐   WebSocket /ws    ┌──────────────────────────────────────────────────────────┐
 │ React UI                   │◀── state 10 Hz ────│ server/app.py  Observatory                               │
 │  • sky map (canvas 2D)     │── commands+seq ───▶│  • SessionManager: control authority, replay protection  │
 │  • 3D mount (three.js)     │   REST /api/*      │  • SimClock (live or simulated time, rate ≤ 3600×)       │
 │  • controls / telemetry    │◀── catalog, sky, ──│  • 20 Hz loop: controller.update(sim_t, wall_t, epoch)   │
 └────────────────────────────┘   search, object   │                                                          │
                                                   │ astro/engine.py ── Skyfield + DE421 + bundled catalog    │
                                                   │ mount/controller.py ── GoTo, tracking, safety policy     │
                                                   │        │ MountDriver (axis coordinates)                  │
                                                   │        ├─ SimulatedMountDriver (axis.py physics)         │
                                                   │        └─ ProtocolMountDriver ── OSM/1 over TCP/serial ──┼──▶ ESP32 firmware (future)
                                                   │ solve/ (optional) ── synthetic camera + tetra3            │     or fake_firmware.py
                                                   └──────────────────────────────────────────────────────────┘
```

The server is authoritative. It owns time, astronomy, motion control and safety. Browsers render the state they
receive and send intents. All devices see the same mount state because there is only one source of it.

## Layers

| Layer | Module | Responsibility |
|---|---|---|
| Astronomy | `astro/engine.py`, `astro/catalog.py` | Time conversion (leap-second aware), target resolution, apparent topocentric Alt/Az with refraction, RA/Dec, rise/transit/set, magnitudes, field rotation, the ICRS→horizon matrix for the sky map |
| Session | `mount/sequencer.py` | Observing-session queue on top of the controller: GoTo, track for a time, next; pauses on any other motion command (controller `command_serial`), skips refused targets; persisted in `state/queue.json` |
| Orbits | `astro/orbits.py` | User-supplied TLE (SGP4) and MPC comet/minor-planet elements (two-body); `state/orbits.json`; resolved by the engine as `sat:`, `comet:`, `asteroid:` targets |
| Recording | `server/recorder.py` | Telemetry JSONL files in `state/logs/` (5 Hz samples + events) for replay in the browser |
| Control | `mount/controller.py` | State machine (idle, slewing, tracking, manual, homing, E-stop, fault, disconnected); GoTo with cable-wrap-aware azimuth choice; tracking with velocity feed-forward; backlash compensation; pointing model (sync offset and plate-solve corrections); safety checks |
| Driver API | `mount/driver.py` | `MountDriver` ABC: `connect`, `capabilities`, `status`, `move_to(alt, az, rates)`, `set_rates`, `stop`, `emergency_stop`, `reset`, `home`, `sync_position`, `update` |
| Simulation | `mount/axis.py`, `mount/sim_driver.py` | Deterministic per-axis motion (bounded velocity/acceleration, discrete-time braking, setpoint extrapolation), backlash, missed steps, limit switches, latency, encoder noise, tilt and zero-point errors, faults |
| Hardware path | `mount/protocol.py`, `protocol_driver.py`, `fake_firmware.py` | OSM/1 ASCII line protocol, a host-side driver, and a simulated microcontroller endpoint |
| Server | `server/app.py`, `server/sessions.py` | REST queries, WebSocket telemetry and commands, control authority, config persistence (`state/config.json`), static UI |
| Plate solving | `solve/` | Experimental: render a star field, detect and solve it with tetra3, compute an alt/az correction |
| UI | `frontend/src` | Sky map (stereographic projection, client-side Earth-rotation extrapolation), 3D mount, panels, responsive layout |

### Time model
- Everything runs on *simulation time* (Unix seconds). In live mode it equals wall-clock UTC. In simulated mode it
  starts from an arbitrary anchor and runs at `rate`× speed.
- The controller advances in fixed control steps of simulation time: 0.1 s normally, 1 s during smooth tracking.
  The simulator subdivides each step into physics steps (20 ms, or 0.2 s in steady state). An accelerated run
  therefore behaves exactly like a real-time one, only faster.
- Setting the clock increments `epoch`. The controller treats that as a discontinuity: it re-plans and re-acquires
  the target, or stops if the target has set.
- Dead-man timers (manual slew) use **wall time**, so they stay safe at any simulation speed.

### Coordinate conventions
- Horizon frame: x = North, y = East, z = Up. Azimuth runs from North through East.
- Mount axis coordinates: the altitude axis angle, and a multi-turn azimuth axis angle bounded by cable-wrap limits
  (default ±270°).
- Pointing model: `mount = sky + offset`. The offset comes from Sync, or from plate-solve corrections.

## Technology research and selection

| Candidate | Evaluation | Decision |
|---|---|---|
| **Skyfield** (MIT) | Pure Python, accurate (IAU 2000A nutation, DE ephemerides), offline with `skyfield-data`, vectorised, small | **Chosen** for all astronomy |
| Astropy (BSD) | Authoritative (ERFA/SOFA), heavy, needs IERS downloads for best accuracy | Used **only** as an independent cross-check in tests |
| Stellarium / Stellarium Web Engine (GPL-2.0 / AGPL-3.0) | Excellent planetarium, but copyleft and large, and hard to embed in a small MIT web UI | Not used. The sky map is our own (canvas). Stellarium can still drive the mount later through LX200/ASCOM adapters |
| OnStepX (GPL-3.0) | Mature telescope firmware (alt-az supported, LX200 protocol, ESP32 support) | Recommended as an **alternative firmware path**. An LX200 adapter driver is planned (see hardware.md) |
| INDI (LGPL) / ASCOM Alpaca (open spec) | Standard telescope-control ecosystems | **Alpaca Telescope device implemented** (`server/alpaca.py`, see alpaca.md). INDI is not planned yet |
| astrometry.net (BSD/GPL parts, large index files) | Gold-standard plate solver, but heavy and needs GB-sized indexes | Not used now |
| **tetra3 / cedar-solve** (Apache-2.0) | Fast lost-in-space solver with a bundled database (10–30° FOV) and pure Python | **Chosen** for the optional plate-solving proof of concept |
| Star data: Hipparcos via **d3-celestial** (BSD-3) | Ready-made mag ≤ 6 star list, names, constellation lines, Messier list | **Chosen** and converted to a compact JSON committed to the repo |
| **FastAPI + WebSockets** | Simple, typed, async; one process, no external services | **Chosen** |
| **React + TypeScript + Vite + three.js** | Maintainable UI, solid 3D | **Chosen**. No UI component library, to keep the bundle small and the look custom |

Rejected alternatives:
- Microservices or a message broker: unnecessary for one mount and a handful of clients.
- Browser-side astronomy for authoritative values: the server stays the single source of truth. The browser only
  extrapolates Earth rotation for smooth display.
- Rendering the sky in three.js: a 2D canvas with stereographic projection is simpler and clearer, and fast enough
  for about 5000 stars.

## Control authority and synchronisation
- Exactly one *controller* session at a time. It expires 15 s after its last message (the UI pings every 3 s).
  Others are observers. Taking over from an active controller requires an explicit "force" action, which the UI
  confirms with the user.
- **E-stop and Stop are accepted from every client**, including malformed or replayed E-stop messages.
- Every command carries a per-session increasing `seq`. Replays are rejected. A reconnect creates a new session
  with no control, so nothing queued on an old connection can move the mount.
- Manual motion is dead-man protected (it must be refreshed within 1 s). It stops when control changes hands or the
  controller disconnects.
- **ASCOM Alpaca clients** are sessions too (one per address + `ClientID`, created by `Connected=true`). They take
  control automatically only when nobody holds it, and every Alpaca request refreshes their lease. `AbortSlew` counts
  as a stop and is accepted from every client. Alpaca handlers are `async` so they run on the event-loop thread,
  like the control loop. Details: [alpaca.md](alpaca.md).

## Extensibility
- **Equatorial mounts**: add a `mount_type` capability and an HA/Dec target transform in the controller. The
  driver API is already axis-generic.
- **New drivers** (OnStepX/LX200, INDI, Alpaca client): implement `MountDriver`. The controller and UI stay
  unchanged.
- **Payload profiles** (`config.PAYLOADS`): mass and inertia scale the acceleration, and long optics reduce the
  maximum altitude. The 3D view picks the matching shape.
