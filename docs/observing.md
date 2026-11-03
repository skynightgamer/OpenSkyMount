# Observing sessions, satellites, comets, recordings, gamepad and phone use

All features below run against the simulator and are covered by automated tests (see TEST_RESULTS.md). None of them
has been tried on real hardware.

## Observing session (target queue)

A list of targets the mount works through on its own: GoTo, track for a set time, next.

* Add targets with **+ Session** in the target panel (10 minutes of tracking), from a satellite's pass list (start at
  the pass's rise, track until it sets), or with **Add the selected target with options** in the Session panel:
  tracking time, *until the next item's start time*, and an optional start time (the mount waits, still tracking the
  previous target, until then).
* **Start session** works through the items in order. **Pause** stops working through the list (the mount keeps
  tracking), **Skip** ends the current item. **Run again** marks all items pending; **Clear finished**/**Clear all**
  tidy up. The queue is stored in `state/queue.json` and survives a restart (an item that was running is shown as
  *interrupted*).
* Every GoTo goes through the normal controller checks. An item whose GoTo is refused (below the altitude limit,
  Sun exclusion zone, unreachable) is marked *skipped* with the reason and the session continues.
* If the controller ends tracking itself (target set below the altitude limit, entered the Sun exclusion zone,
  cable-wrap limit without unwind), the item ends early with that reason and the session moves on.
* **Any other motion command pauses the session**: GoTo, Stop, E-STOP, manual slew, Home, Reset, from any client
  including Alpaca. A latched E-stop or fault pauses it as well. The session never resumes by itself; a person has to
  press Start again (the interrupted item then starts over).
* Optional: *Record telemetry while the session runs* starts a recording with the session and stops it when the
  session completes.

Commands (WebSocket, need control): `queue_add {target, start_at?, duration_s?}` (`duration_s: null` = until the next
item's start time), `queue_remove {id}`, `queue_move {id, delta}`, `queue_clear {finished_only?}`, `queue_reset`,
`queue_start {record?}`, `queue_pause`, `queue_skip`. The state message carries `session`.

## Satellites (TLE), comets and asteroids (MPC elements)

OpenSkyMount does not download element sets (it is local-first and has no network dependency). Paste them, or load
a file, in the **Satellites, comets & asteroids** panel, or `POST /api/orbits` with plain text or `{"text": ...}`.

* **Satellites**: two-line elements, with or without a name line (also the `0 NAME` 3LE variant), e.g. CelesTrak's
  "stations" group. Checksums are verified. Positions use SGP4 through Skyfield. TLEs go stale within days to weeks;
  the target panel shows the element age, and older than 14 days a warning is shown and logged on GoTo.
* **Comets**: lines in the MPC `CometEls.txt` format. **Asteroids**: lines in the `MPCORB.DAT` format. Both are
  propagated as two-body orbits around the Sun (as Skyfield's own MPC helpers do); planetary perturbations are
  ignored, so accuracy degrades away from the element epoch. Magnitudes are estimated from the MPC parameters
  (comets: m = g + 5 log Δ + 2.5 k log r; asteroids: H, G).
* Target ids: `sat:<NORAD number>`, `comet:<designation>` (e.g. `comet:2P/Encke`), `asteroid:<number or designation>`.
  They also appear in search, on the sky map (first 50 loaded objects; satellites only while above the horizon,
  refreshed every second while one is up) and in the session queue.
* Satellite passes: the target panel lists the passes of the next 48 h above 10° (or the configured minimum target
  altitude, if higher) with maximum altitude and whether the satellite is sunlit (visible to the eye when sunlit
  and the sky is dark). `GET /api/passes?id=sat:25544&hours=24&min_alt=10`.
* Elements are kept in `state/orbits.json`. `DELETE /api/orbits?id=…` removes one object, `DELETE /api/orbits` all;
  removing the object the mount is following is refused.

### Fast tracking

Satellites move up to about 1° per second across the sky. The controller samples their path every 0.25 s (stars:
2 s), skips the 1 s steady-state control steps, leads its setpoints by the driver's command latency, and picks the
azimuth wrap at GoTo so that the whole pass (until the target drops below the altitude limit, up to 20 min) fits
inside the cable-wrap limits; otherwise an unwind in the middle of a pass would lose the satellite for most of a
minute. In the simulator a 70° ISS pass is followed with a worst true error below 30″ (about 12″ RMS) at axis rates
up to 2.5°/s; a pass close to the zenith exceeds the azimuth rate limit and lags (flag `rate_limited`), as for stars.

## Telemetry recordings and replay

**Telemetry recordings → Start recording** (or the session option) writes `state/logs/<UTC time>[-label].jsonl`:
a header line, 5 samples per second (simulation time, mode, target and its alt/az, estimated pointing, raw axes, axis
rates, reported error, simulator ground-truth error, flags) and the event log. About 2.5 MB per hour; a file stops at
200 MB.

**Replay** opens a recording in the browser: tracking error over time (reported, or the simulator's true error),
coloured mode band, event markers, a scrubber with the full state at any moment, per-target statistics (RMS, 95 %,
maximum, true RMS, maximum axis rate, computed from tracking samples only) and CSV export. `GET /api/logs` lists the
files, `GET /api/logs/<name>` downloads one, `DELETE /api/logs/<name>` removes it.

## Gamepad

Any controller the browser exposes through the Gamepad API (standard layout: Xbox, PlayStation, many Bluetooth
pads). Press a button once so the browser makes it visible; the Motion panel shows its name.

| Control | Action |
|---|---|
| hold **LB** or **RB** | dead man: motion is only possible while one is held |
| left stick | alt (up/down) and az (left/right), proportional with a dead zone |
| D-pad | full selected speed in that direction |
| **Y** / **X** | next / previous speed step |
| **B** | Stop (works without control and without the dead man) |
| **Back / View / Select** | E-STOP (works without control) |

The rates are re-sent every 250 ms while held; the server's 1 s dead-man timer stops the slew if they stop arriving
(tab hidden, pad unplugged, connection lost). Releasing the button sends an explicit stop.

## Phone use: hand controller and home-screen app

* The **Hand** tab on phones (**Hand controller** button on larger screens, or open `/?view=hand`) shows a large
  E-STOP, Stop, Track/Reset, a big press-and-hold direction pad, the speed steps, the current mode/target/error and
  the session (start, pause, next). The last view is remembered per device.
* The app has a web manifest and icons, so phones can add it to the home screen. The service worker (offline app
  shell; it never caches the API) is only allowed by browsers in a secure context, i.e. on `localhost` or over HTTPS.
  Over plain HTTP on a LAN, "Add to Home Screen" still creates a launcher on most phones, but the browser may show it
  as a bookmark instead of an installed app. Without a connection to the server the app shows itself as disconnected;
  it cannot control anything offline.
