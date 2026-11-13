# Next tasks (ordered)

00. Session 12 follow-ups: check the figures' frame rate on a low-end phone (the mesh is cut coarser when small; a
   per-figure cache while the view is still would help if needed); optional label epithets ("Orion · the Hunter"; the
   English names are already in `figures.json`); click a figure to select its constellation; a short myth blurb per
   figure; decide whether to bundle the full Free Art License text next to `NOTICE.md`; separate Puppis/Vela/Serpens
   art if a free source appears.
0. Session 10 follow-ups: HTTPS (task 7) matters more now that accounts exist; optional per-user API tokens for
   scripts instead of the shared password; check the skyline layer's frame rate on a low-end phone; audit the welcome
   screen, guide and account dialog with a screen reader; check guide dragging and wheel/pinch zoom on real touch devices.
1. Session 6 follow-ups: try a real gamepad (Xbox/PlayStation) and installing the app on a real phone (needs HTTPS,
   task 7, for the service worker); load real CelesTrak/MPC files and compare a satellite pass with a sky observation;
   editable queue items (duration/start time) and per-item "GoTo only"; visual magnitudes for satellites.
2. Alpaca follow-ups: run ASCOM ConformU against `/api/v1/telescope/0` (needs a machine that can download it) and
   test with N.I.N.A.; then `MoveAxis` with a dead-man design (D32) and `PulseGuide`.
3. OnStepX adapter on real firmware (D48, docs/hardware.md checklist): direction of `:Mw#`/`:Mn#` in alt-az mode,
   small `:RA`/`:RE` rates with tracking off, `:GA#`/`:GZ#` without OnStep's model/refraction, `:MA#` codes and the
   `:GU#` flags; then measure tracking error. A pulse-guide (`:Mg`) based fine loop would add a firmware-side dead
   man if OnStep renews pulses smoothly. **Blocked by hardware.**
4. Session 11 follow-ups: audit the settings sheet with a screen reader; consider a "Follow mount" toggle in the
   map toolbar instead of the layer row.
5. Plate solving from uploaded real images (camera frames), with distortion-tolerance tests on public-domain sky
   images.
6. Pointing model: add cone-error and axis non-perpendicularity terms once real measurements exist.
7. HTTPS for LAN mode (self-signed certificate or a reverse-proxy guide) so the password is not sent in clear text.
8. Physical bench test with an ESP32 implementing OSM/1 (see the docs/hardware.md integration plan). **Blocked by
   hardware.**

## Completed follow-ups

- Fixed the idle simulator's blinking mount crosshair and added a browser regression at maximum sky-map zoom-out.
- Removed star blinking in the sky map and added a browser regression for steady brightness and size.
