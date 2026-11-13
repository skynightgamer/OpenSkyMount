# Next tasks (ordered)

000. Known simulator issue (found with the alignment wizard): a GoTo to a target that barely moves, such as Polaris,
   can stall 0.05° short (the backlash compensation side is set from the target's rate, not from the side the axis
   engaged; at 20:00 UTC on 2026-11-01 from Berlin it never settles). The wizard avoids poles; fixing it means setting the
   side from the measured motion, and the obvious one-line change (ignore slow rates while slewing) breaks
   `test_three_star_model_corrects_whole_sky` and the sequencer test, so it needs its own session.
000. Session 13 follow-ups: check the new panels (Sky events, Mosaic & timelapse, the alignment wizard, the slew
   preview) and the Night-mode brightness slider with a screen reader and on a real phone; try the mosaic frames and the
   alignment on real hardware (all times are estimates from the configured axis limits); a "Follow mount" or "Go to
   time" convenience in the events list for the real clock; solar eclipse contacts and the Sun's path for the eclipse
   events (only the maximum is computed); meteor shower radiant drawn on the sky map; a second Alpaca-free way to
   export a mosaic (CSV of RA/Dec) for other planners; check why the Night filter costs about 20 % CPU while idle and
   whether a canvas-native red palette would be cheaper.
00. Session 12 follow-ups: check the figures' frame rate on a low-end phone (the mesh is cut coarser when small; a
   per-figure cache while the view is still would help if needed); optional label epithets in the sky map's labels
   (English names are shown under the Latin ones when zoomed in); decide whether to bundle the full Free Art License
   text next to `NOTICE.md`; separate Puppis/Vela/Serpens art if a free source appears.
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
- Cut idle CPU: the sky map caches its static layer and redraws on change (idle ~1 present/s) and the 3D view renders on
  demand (D52). Still open: measure on a low-end phone, and a per-figure cache would cut the cost of panning at night.
- Night-mode brightness slider, clickable constellation figures with English names and stories, the sky events
  calendar, the slew preview with the cable-wrap indicator, the guided star alignment and the mosaic/timelapse planner
  (session 13; D53 to D58).
