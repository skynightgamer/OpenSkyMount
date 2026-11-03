# Next tasks (ordered)

1. Alpaca follow-ups: run ASCOM ConformU against `/api/v1/telescope/0` (needs a machine that can download it) and
   test with N.I.N.A.; then `MoveAxis` with a dead-man design (D32) and `PulseGuide`.
2. Show Alpaca connection details (address, port, device 0) in the Settings panel.
3. LX200 / OnStepX driver adapter (`mount/onstep_driver.py`) with a fake LX200 endpoint for tests.
4. Plate solving from uploaded real images (camera frames), with distortion-tolerance tests on public-domain sky
   images.
5. Pointing model: add cone-error and axis non-perpendicularity terms once real measurements exist.
6. HTTPS for LAN mode (self-signed certificate or a reverse-proxy guide) so the password is not sent in clear text.
7. Physical bench test with an ESP32 implementing OSM/1 (see the docs/hardware.md integration plan). **Blocked by
   hardware.**
