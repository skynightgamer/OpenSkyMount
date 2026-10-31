# Next tasks (ordered)

0. **Blocking for persistence (owner action)**: grant the Claude GitHub App write access to
   `skynightgamer/OpenSkyMount` (https://github.com/apps/claude/installations/select_target), then push the branch
   `claude/gallant-turing-py2w4q`.
1. Multi-star pointing model (azimuth/altitude zero, base tilt, cone error) fitted from ≥ 3 Sync or plate-solve
   points. Replace the single offset in `controller.py`. Test with the `sim.tilt_*` errors.
2. ASCOM Alpaca Telescope device endpoint (`/api/v1/telescope/0/...`) so N.I.N.A., Stellarium and similar can drive
   OpenSkyMount. Read-only properties first, then SlewToCoordinatesAsync, AbortSlew and Tracking.
3. LX200 / OnStepX driver adapter (`mount/onstep_driver.py`) with a fake LX200 endpoint for tests.
4. UI: field-rotation and maximum-exposure helper for astrophotography; a "tonight's best targets" list (objects
   above 30° during darkness); unit toggle (decimal or sexagesimal).
5. Proper motion and a fainter star tier (mag 7–8) for zoomed views (keep the license BSD/PD).
6. Optional authentication token for LAN mode (`--token`).
7. Plate solving from uploaded real images (camera frames), with distortion tolerance tests on public-domain sky
   images.
8. Physical bench test with an ESP32 implementing OSM/1 (see docs/hardware.md integration plan). **Blocked by
   hardware.**
