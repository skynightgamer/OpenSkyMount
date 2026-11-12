# Progress

_Last updated: 2026-11-12 (session 11)._ Main branch: `main` (sessions 6–11 merged; session 11 merged from `feat/session-11-polish-onstep`).

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
| Welcome screen, simulator setup guide | **Done, verified (sim, browser E2E)** | First-run welcome, 7-step docked guide with live completion checks. See D46 |
| Accounts (log in/out, roles, account management) | **Done, verified (tests + browser E2E)** | Admin/operator/observer, first-admin setup from the server computer, profile, password change, logins list, user admin, CLI. See D47 |
| OnStepX / LX200 adapter | **Experimental; verified against a fake endpoint only** | `onstep://host:9999`; firmware GoTo for long moves, rate loop for tracking, direction and wrong-way-round checks. Real OnStepX: blocked by hardware. See D48 |
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
- **Session 7 (2026-11-04)**: user feedback: sleeker, more realistic phone model and a more compelling sky map (branch
  `feat/visuals-phone-skymap`). (1) **Phone model** rebuilt (D42): slim titanium-style frame with rounded edges,
  frosted back glass, raised camera plateau with three lenses (main lens on the pointing axis), flash, LiDAR, side
  buttons, USB-C port and speaker holes, antenna bands, a lit camera-app screen with a dynamic-island cut-out, and a
  real spring clamp (guide rods, sliding jaws with rubber pads and lips, mounting block with thumb screw). The 3D view
  now zooms to the cursor and allows closer zoom (0.12 m) to inspect the payload. (2) **Sky map** (D41,
  `src/sky/skyrender.ts`): sky colour by Sun altitude with a warm twilight glow toward the Sun, blue day sky with a
  sun halo, moonlit brightening and a horizon glow; the **Milky Way** from the Milky Way Outline Catalog
  (`scripts/build_milkyway.py` → `src/sky/milkyway.png`, new "Milky Way" layer toggle, fades in twilight and
  moonlight); glow sprites for bright stars, atmospheric extinction near the horizon; constellation lines that stop
  short of their stars; DSO symbols by type (galaxy, open/globular cluster, nebula, planetary); Moon drawn with its
  real phase and the bright limb toward the Sun; planet glows. README screenshots regenerated.
  9 new frontend unit tests (Milky Way map checked against galactic coordinates and the Sagittarius/Scutum star
  clouds, sky colour model, Moon bright-limb direction); `@types/node` added as a dev dependency for that test.
  118 backend tests (8 skipped, optional extras not installed), 33 frontend unit tests and all three browser E2E
  scripts pass. Measured ~8 ms of script time per frame while panning the phone-size sky map (headless Chromium).
  **Not verified**: frame rate on a real phone.
  Follow-up: phone enlarged to the largest current class (6.9″, 163 × 77.6 × 8.25 mm); camera plateau, lenses and
  buttons scale with it.
- **Session 8 (2026-11-07)**: user feedback: make the UI more intuitive, especially the right (control) column (branch
  `feat/ui-control-panel`, D43). (1) **Sticky control head**: control authority ("View only: nobody has control" is
  highlighted), a live status line (mode, target, pointing error), E-STOP and Stop. They stay on screen while the column
  scrolls (also on the phone Control tab). While E-STOP/fault/disconnect is latched, a red banner says why motion is
  blocked and holds the Reset button. (2) **Collapsible sections** with a one-line summary in every header (target name,
  speed, "2 targets · running", "not aligned" / "3 points · 12″ RMS", clock time and rate, objects loaded, recording,
  latest log message); open/closed remembered per device. Target, Move and Observing session open by default; the whole
  column fits on one screen when the others are collapsed (was ~1660 px). (3) **Target**: warnings and the GoTo/Sync/Add
  buttons come before the data table; altitude, azimuth and rise/transit/set lead the table; below-horizon objects say
  when they rise; a hint says to take control when buttons are disabled for that reason; "+ Session" became "Add to
  session". (4) **Move**: Start/Stop tracking merged into one button that names the target ("Track Jupiter"); manual pad
  shows arrows with the axis underneath; E-STOP/Stop/Reset moved to the head. (5) **Observing session**: actions only
  appear when they apply (no row of disabled buttons on an empty queue); secondary actions as text links. (6) **Time**:
  "Live" highlighted when live, "Jump to" label, note that time warp is simulator-only.
  E2E scripts updated (Time section collapsed by default, renamed button) and extended: E-STOP stays in view after
  scrolling the column; section state survives a reload. 118 backend tests (8 skipped, optional extras not installed),
  33 frontend unit tests and all three browser E2E scripts pass (macOS, Python 3.15, local Playwright 1.48 + Chromium).
  **Not verified**: on a real phone, with a screen reader.
- **Session 9 (2026-11-07)**: user feedback: make the UI more beautiful; drop "nobody has control" (branch `feat/ui-polish`,
  D44). Restyle only, no behaviour changes. (1) **Palette**: blue-black night colours that match the sky map instead of
  neutral greys; 3D view background and ground disc re-tinted to match. (2) **Frame**: sky, mount, control and status bar
  are rounded panes on a darker backdrop; top bar with a logo mark, location pin, connection pill and icon buttons (labels
  hidden below 1000 px; accessible names unchanged). (3) **Views**: sky-map zoom/compass buttons and the 3D legend as
  translucent overlay groups; sky layers as chip toggles (still checkboxes); search field with an icon and a floating
  results list. (4) **Controls**: segmented controls for manual speed, clock rate and the "Best tonight" filter; manual
  pad on a base plate with chevron icons; section chevrons moved to the right of the summary; mode dots in the control
  head and status bar; target warnings as a callout, horizon state as a pill; modal, toasts and login restyled.
  (5) Authority line now reads "View only" when nobody holds control ("View only: <name> has control" unchanged).
  118 backend tests (8 skipped, optional extras not installed), tsc and 33 frontend unit tests pass; checked by eye in
  the in-app browser at 1440 × 900 and phone width. **Not run**: browser E2E scripts (no Playwright browser set up this
  session; selectors and button names were kept). README screenshots (`docs/img`) still show the session 8 styling.
- **Session 10 (2026-11-12)**: user request: more compelling sky map, a simulator setup guide, a welcome screen, and
  log in/out/account management. Worked directly on `main` (user's instruction).
  (1) **Sky map** (D45, `src/sky/scenery.ts`): smooth camera flights (search, "Best tonight", compass buttons, new
  centre button, double-click), eased "Follow mount"; decorative skyline layer ("Landscape": distant ridge, hills and
  procedural trees ≤ ~3°, coloured by Sun altitude, true horizon still drawn); ecliptic layer; airglow band; bright-star
  twinkle (stronger low down); selection brackets with a name tag; animated target ring; mount → target line while
  slewing; fading mount trail; status pill. Respects `prefers-reduced-motion`. Layer choices remembered per device.
  Found and fixed in E2E: drawing into a zero-size canvas while the sky tab is hidden on phones threw every frame.
  (2) **Welcome screen + simulator setup guide** (D46, `Welcome.tsx`, `Guide.tsx`).
  (3) **Accounts** (D47, `server/accounts.py`, `server/auth.py`, `server/account_routes.py`, `Account.tsx`): login
  screen with user name, "keep me logged in", shared-password fallback; account menu in the top bar; account dialog
  (profile, password and logins with per-device log out and "log out everywhere", user admin: add with generated
  password, role, reset password, delete); first-admin setup; observer role enforced on WebSocket and REST;
  command-line tool for a forgotten admin password. Existing password mode and Alpaca HTTP Basic unchanged.
  136 backend tests (+18 accounts; 8 skipped, optional extras not installed), 42 frontend unit tests (+9 scenery) and
  all four browser E2E scripts (new `frontend/e2e/accounts.cjs`) pass; README screenshots regenerated.
  **Not verified**: on a real phone, with a screen reader, frame rate on low-end devices with the skyline layer.
- **Session 11 (2026-11-12)**: user request: tidy the settings panel and animate it like the account menu, a better
  selection ("focus") animation on the sky map, finish "alpha Lyr…" in the search hint, fix the sky-layer row, and
  NEXT_TASKS items 3 and 4. Branch `feat/session-11-polish-onstep`, merged into `main` at the user's request.
  (1) **Settings sheet** (D49, `Settings.tsx`, `presence.ts`): drops from the top bar with the account menu's
  animation and now also animates out (the account menu too); categories Display / Location / Mount & safety /
  Simulator / Connections; grouped rows with hints, switches, units; fixed footer with Cancel/Save and status
  (unsaved, needs control); unsaved dots per category; Escape and backdrop cancel, focus returns to the button; the
  guide's "Enter coordinates…" opens the Location page. Phone: full-width sheet, categories as a scrolling row.
  (2) **Sky-layer row**: chips wrap on the left, "Follow mount" stays pinned top-right behind a divider (it used to
  drop alone onto the second row's right edge).
  (3) **Selection reticle**: four arcs that settle in with one soft ripple, glide along the great circle to the next
  selection and resize, sized by brightness, name tag rises in; replaces the rotating cyan corner brackets
  (`reticleAt`/`slerp` in `scenery.ts`, 4 new unit tests).
  (4) **Search**: placeholder reads "alpha Lyrae"; the catalog now also finds genitive designations ("alpha Lyrae",
  "beta Ursae Majoris", "61 Cygni", "alpha Centauri" → α1 Cen, "alpha2 Cen", "Boötis" with or without the diaeresis).
  (5) **Alpaca connection details** (NEXT_TASKS 3): `GET /api/alpaca` (addresses, port, device 0, URL, discovery
  state, login, connected clients) shown in Settings → Connections with copy buttons and a note when the server only
  listens on this computer; `create_app(listen=…)` is passed by the CLI.
  (6) **OnStepX/LX200 adapter** (NEXT_TASKS 4, D48): `mount/lx200.py`, `mount/onstep_driver.py`,
  `mount/fake_onstep.py` (+ `openskymount-fake-onstep`), `--driver onstep://host[:port]`. 12 new tests over TCP:
  GoTo/manual/E-stop through the controller, tracking a moving setpoint with rates only (< 15″), multi-turn azimuth
  through the firmware's 0/360 seam with GoTo legs, sync offset, reversed wiring detected and stopped, host watchdog,
  link loss and reconnect, handshake (equatorial refused, precision toggle), firmware GoTo refusal and wrong-way-round
  turns become faults, strict solar policy. Found while testing: if the firmware's azimuth limits force the long way
  round, re-issuing the GoTo would loop forever; now a `goto_rejected` fault.
  149 backend tests (8 skipped, optional extras not installed), 46 frontend unit tests and all four browser E2E
  scripts pass; README screenshots regenerated. **Not verified**: real OnStepX firmware (no hardware), the settings
  sheet with a screen reader.
