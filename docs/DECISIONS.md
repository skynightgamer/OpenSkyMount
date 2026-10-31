# Decision log

| # | Decision | Reason | Alternatives considered |
|---|---|---|---|
| D1 | Python + FastAPI backend, React + TS + three.js frontend, one process | Matches the proposed stack, simple local deployment, typed | Node backend (weaker astronomy libraries), microservices (unneeded) |
| D2 | Skyfield + DE421 (`skyfield-data`) for astronomy, offline | Accurate, MIT, no network at runtime | Astropy (heavy, IERS downloads) is kept as a test-only cross-check |
| D3 | Server-authoritative simulation and control | One source of truth for all devices; the same controller drives real hardware | Simulating in the browser (would desync clients and duplicate logic) |
| D4 | Driver API in axis coordinates, astronomy and tracking on the host | Firmware stays tiny and testable; the simulator and hardware are interchangeable | Firmware-side astronomy like OnStepX (supported later through an adapter) |
| D5 | Position + rate setpoints, extrapolated by the axis between commands | Smooth tracking with sparse setpoints and robust to latency | Pure velocity streaming (drifts), position-only (stair steps) |
| D6 | Setpoints refer to the start of each control interval | Fixed a one-interval lead (16″ error) found by the headless demo; regression test added | — |
| D7 | Fixed-step control in simulation time; 1 s steps in smooth tracking, 0.2 s physics steps in steady state | Time acceleration up to 3600× without behaviour change; about 8× faster tests | Variable-step integration |
| D8 | Multi-turn azimuth ±270° with automatic unwind | Realistic cable management; avoids long slews across north | Unlimited azimuth (needs a slip ring) |
| D9 | Solar GoTo refused by default and always refused off-simulator; 15° exclusion zone | Safety requirement | Allow with a warning (rejected) |
| D10 | Single control lease + per-session seq numbers; E-stop/Stop from anyone | Prevents conflicting or stale commands; safety first | Multi-controller merging |
| D11 | Dead-man timers in wall time | Must not expire faster at high simulation rates | Simulation time |
| D12 | Catalog from d3-celestial (BSD-3), committed as compact JSON | License-compatible, offline, includes names and constellation lines | HYG (CC BY-SA, share-alike), Stellarium skycultures (GPL) |
| D13 | Own canvas sky map (stereographic) instead of embedding Stellarium Web Engine | AGPL/GPL incompatibility with MIT, size, control over interaction | Stellarium Web Engine, d3-celestial runtime |
| D14 | OSM/1 ASCII line protocol + fake firmware | Trivial to implement on an ESP32; executable spec; testable over TCP | JSON over serial (heavier), immediate OnStepX dependency (GPL firmware, different model) |
| D15 | Plate solving with cedar-solve/tetra3 as an optional extra, installed `--no-deps` with a numpy-2 shim | Apache-2.0, bundled database, fast; its version pins block Python 3.13 | astrometry.net (GB indexes), writing a custom solver (discouraged) |
| D16 | MIT license | Compatible with all bundled data and dependencies | GPL (would allow embedding Stellarium/OnStepX code, but limits reuse) |
| D17 | No UI component library | Small bundle, custom dark astronomy look | MUI/Chakra (heavier) |
| D18 | Berlin as the default site | A neutral, valid default; the user sets their own site in Settings | Geolocation required (rejected: must be optional) |
