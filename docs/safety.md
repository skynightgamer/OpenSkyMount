# Safety

OpenSkyMount will eventually move real motors near people, cables and expensive optics, and possibly near the Sun.
Software is only one layer of protection. **A software emergency stop does not replace a hardware emergency stop.**

## Solar safety (implemented)
- Automatic slewing to the **Sun is refused by default** (`control.allow_solar_targets = false`).
- A **solar exclusion zone** (default 15°) applies to GoTo targets, to the GoTo path (checked by sampling the
  straight alt/az path), to targets during tracking (re-checked every 30 s, e.g. for the Moon near the Sun), and to
  manual slews (motion that brings the payload closer to the Sun is stopped).
- Even with `allow_solar_targets = true`, solar GoTo is allowed **only with the simulated driver**. On any hardware
  driver the controller refuses it unconditionally. This is covered by the test
  `test_solar_policy_is_strict_on_hardware_path`.
- Enabling solar observation on hardware would need an independently verified configuration: a certified
  full-aperture solar filter, physically secured, with a filter-presence sensor, finder caps, and a review of
  failure modes. That is out of scope for this version.

## Motion safety (implemented in software and simulation)
| Hazard | Mitigation |
|---|---|
| Runaway after client loss | Manual slews need a refresh every 1 s (wall time). Control change or disconnect stops manual motion. The firmware watchdog stops the axes after 2 s without host traffic (OSM/1, tested with the fake firmware) |
| Stale or duplicated commands | Per-session sequence numbers; a reconnect requires taking control again |
| Conflicting operators | Single control authority with a lease; takeover needs explicit confirmation. ASCOM Alpaca clients join the same scheme: they take control only when it is free and never by force |
| External programs (Alpaca) | Alpaca commands run through the same controller checks (solar exclusion, limits, faults, E-stop latch). `AbortSlew` is accepted from every client. Alpaca `MoveAxis` is not offered because it has no dead-man refresh |
| Emergency | E-stop from any client, executed immediately with no command latency, latched until Reset |
| Travel limits | Controller soft limits, then firmware soft limits (braking profile never passes them), then hard limit switches 2° beyond (fault and stop) |
| Cable wrap | Multi-turn azimuth bounded to ±270°, automatic 360° unwind when tracking reaches the limit |
| Lost steps / position uncertainty | Axis encoders are compared with the step count; a mismatch above 0.5° raises `sensor_disagreement`, stops motion and needs Reset or Home |
| Homing failure | `homing_failed` fault; GoTo is refused until resolved |
| Overcurrent | `overcurrent` fault halts both axes immediately |
| Configuration change during motion | Changing the site, payload or limits stops motion |

## Hardware duties (must be implemented in the physical build)
1. **Hardware E-stop**: a latching mushroom switch that cuts motor-driver power (VMOT) directly, independent of the
   microcontroller.
2. **Motor current limits** set on the drivers (e.g. TMC2209 IRUN), sized so a stalled axis cannot damage gears or
   payload.
3. **Limit and home switches** wired fail-safe (normally closed), so a broken wire reads as "limit reached".
4. **Safe startup**: motors disabled until the host connects and the mount is homed or synced. No motion on power-up.
5. **Brown-out handling**: on supply sag, disable the drivers and mark the position as unknown.
6. **Mechanical**: balance the payload, use clutches or slip couplings where possible, and add cable strain relief.
7. Do not use unrestricted remote shell access on the device. Expose only the validated command API.

## Known gaps
- The software has not run on any physical hardware. The behaviour of real drivers, sensors and power systems
  remains unverified.
- Path checking against the Sun samples a straight alt/az path. Real slew paths are close to that but not identical.
- Authentication is optional and basic: user accounts (`state/accounts.json`, scrypt password hashes, file mode
  0600) or a shared `OPENSKYMOUNT_PASSWORD` (or `--password`) require a login for the UI, the REST API and the
  WebSocket. It runs over plain HTTP (no TLS), so passwords and session cookies can be read by anyone who can sniff
  the network. Use it on a trusted local network only; the default `--host 127.0.0.1` needs no login. Failed logins
  are limited to 5 per minute per address. Sessions are in memory (a restart logs everyone out); deleting an account,
  resetting its password or "log out everywhere" ends its sessions and closes its WebSockets at once.
- Only the computer running the server (a loopback address), or a browser logged in with the shared password, may
  create the first (admin) account, so nobody else on the network can claim it. Behind a reverse proxy on the same
  machine every request looks local: create the first account before exposing the proxy.
- Roles: observers cannot take control and every non-GET REST call from them is refused (403); E-stop and Stop stay
  available to them. An account demoted to observer loses control immediately. There is always at least one admin.
- `POST /api/estop` deliberately needs no login, so the login screen can always stop the mount. Anyone who can reach
  the server can therefore stop (never move) the mount.
