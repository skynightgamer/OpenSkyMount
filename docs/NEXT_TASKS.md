# Next tasks (ordered)

1. Continuous integration on GitHub (backend and frontend checks, E2E with Playwright).
2. ASCOM Alpaca Telescope device endpoint (`/api/v1/telescope/0/...`) so N.I.N.A., Stellarium and similar can drive
   OpenSkyMount. Read-only properties first, then SlewToCoordinatesAsync, AbortSlew and Tracking.
3. LX200 / OnStepX driver adapter (`mount/onstep_driver.py`) with a fake LX200 endpoint for tests.
4. UI: unit toggle (decimal or sexagesimal) and an explicit time-zone setting.
5. Optional authentication token for LAN mode (`--token`).
6. Plate solving from uploaded real images (camera frames), with distortion-tolerance tests on public-domain sky
   images.
7. Pointing model: add cone-error and axis non-perpendicularity terms once real measurements exist.
8. Physical bench test with an ESP32 implementing OSM/1 (see the docs/hardware.md integration plan). **Blocked by
   hardware.**
