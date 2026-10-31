# Configuration

Settings are persisted in `state/config.json` (created on first change; the directory is set with `--state-dir`).
They can be edited in the UI (⚙, needs control authority) or through the WebSocket `set_config` command. Unknown
keys and wrong types are rejected.

| Section | Key | Default | Meaning |
|---|---|---|---|
| observer | latitude, longitude, elevation_m, name | Berlin (52.52, 13.405, 35 m) | Site. Longitude is east-positive |
| observer | temperature_c, pressure_mbar | 10, 1010 | Refraction model; set both to `null` to disable refraction |
| payload | — | `smartphone` | One of `smartphone`, `compact`, `dslr`, `telephoto`, `telescope`. Scales acceleration (inertia), maximum rate, and maximum altitude (long optics: 82–88°) |
| limits | min_altitude, max_altitude | −5, 90 | Mechanical altitude range (the payload may lower the maximum) |
| limits | az_min, az_max | −270, 270 | Multi-turn azimuth (cable wrap) |
| limits | max_rate, max_accel, tracking_max_rate | 6°/s, 6°/s², 6°/s | Light-payload values before payload scaling |
| control | goto_tolerance_deg, tracking_tolerance_deg | 0.02 | Arrival and tracking tolerances (72″) |
| control | min_target_altitude | 0 | Targets below this are refused, and tracking stops when the target sets below it |
| control | allow_solar_targets | false | See safety.md (simulator only even if true) |
| control | sun_exclusion_deg | 15 | Solar exclusion radius |
| control | track_after_goto, auto_unwind, backlash_compensation | true | Behaviour switches |
| control | manual_deadman_s, control_lease_s | 1, 15 | Safety timers (wall-clock seconds) |
| sim | command_latency_s | 0.05 | Delay before the simulated firmware executes commands |
| sim | backlash_alt, backlash_az | 0.05°, 0.08° | Gear backlash (also used as the calibrated compensation value) |
| sim | az_zero_offset, alt_zero_offset, tilt_magnitude, tilt_direction | 0 | Pointing-model errors that only Sync or plate solving can reveal |
| sim | encoder_noise | 0.002° | RMS noise of the reported encoder angle |
| sim | has_axis_encoders | true | Absolute output-shaft encoders (enable lost-step detection) |
| sim | seed, physics_step_s | 42, 0.02 | Determinism and physics resolution |

Environment variables: `OPENSKYMOUNT_HOST`, `OPENSKYMOUNT_PORT`, `OPENSKYMOUNT_STATE`, `OPENSKYMOUNT_DRIVER`
(`sim` or `tcp://host:port` or `serial:///dev/ttyUSB0@115200`).

## API summary
- `GET /api/health`, `/api/config`, `/api/status`, `/api/catalog`, `/api/sky?t=`, `/api/search?q=`,
  `/api/object?id=&t=`
- WebSocket `/ws?name=`. Client messages: `ping`, `request_control {force}`, `release_control`,
  `command {seq, cmd, args}`. Commands: `goto {target, track}`, `track_start`, `track_stop`, `stop`, `estop`,
  `reset`, `manual {alt_rate, az_rate}`, `home`, `sync {target}`, `clear_sync`, `apply_correction {d_alt, d_az}`,
  `set_clock {live|time|rate|paused}`, `set_config {...}`, `inject_fault {fault, active}`,
  `align_simulated {apply}`.
- Target ids: `planet:jupiter`, `moon`, `sun`, `hip:32349`, `dso:M31`, `radec:<ra°>,<dec°>` (J2000),
  `altaz:<alt>,<az>`, or a plain name such as `Vega`.
