# Firmware — OSM/1 protocol contract

No firmware has been written yet. This document is the contract that a future ESP32 firmware must implement.
The executable reference implementation is `backend/openskymount/mount/fake_firmware.py`, and the host side is
`protocol_driver.py`. Both are covered by `backend/tests/test_protocol.py`.

## Transport
USB-serial at 115200 baud (`--driver serial:///dev/ttyUSB0@115200`, needs `pyserial`) or TCP on port 7624
(`--driver tcp://host:7624`). ASCII lines terminated by `\n`. Every request starts with an integer id that the reply
echoes.

| Request | Reply | Semantics |
|---|---|---|
| `<id> HELLO` | `<id> OK OSM/1 name=… fw=… enc=0/1 home=0/1 vmax= amax= altmin= altmax= azmin= azmax=` | Capability discovery |
| `<id> PING` | `<id> PONG` | Keep-alive |
| `<id> STATUS` | `<id> ST alt az valt vaz ealt eaz flags faults` | Positions (deg), velocities (deg/s), encoder readings or `nan`; flags `H` homed, `h` homing, `E` e-stop, `M` moving; faults comma-separated or `-` |
| `<id> MOVE alt az ralt raz` | `OK` or `ERR LIMIT …` | Position setpoint with rate feed-forward. Firmware extrapolates `target += rate·dt` until the next MOVE |
| `<id> RATE ralt raz` | `OK` | Velocity mode (manual), bounded by vmax and soft limits |
| `<id> STOP` | `OK` | Controlled deceleration |
| `<id> ESTOP` | `OK` | Immediate stop, latched until RESET |
| `<id> RESET` | `OK` | Clear latched faults (re-reference the step counters from the encoders after lost steps) |
| `<id> HOME` | `OK` | Run homing to the switches; `H` flag when done, fault `homing_failed` on failure |
| `<id> SYNC alt az` | `OK` | Redefine the current position |
| malformed | `<id> ERR CODE message` | Codes: SYNTAX, UNKNOWN, ARGS, LIMIT |

Axis coordinates are in degrees. Azimuth is multi-turn (no wrap at 360°) and limited by cable wrap.

## Mandatory firmware behaviour
- Motion profile: bounded velocity and acceleration; a discrete braking law that never overshoots the soft limits.
- **Watchdog**: if no valid request arrives for 2 s, perform a controlled stop (the host polls STATUS at 10 Hz).
- **E-stop latch**: ESTOP stops instantly and ignores motion commands until RESET. The hardware E-stop must cut
  motor power independently of the firmware.
- Report faults: `limit_alt`, `limit_az`, `stall_alt`, `stall_az`, `overcurrent`, `sensor_disagreement`,
  `homing_failed`.
- On power-up: motors disabled (or holding) and not homed; no motion until HOME, SYNC or MOVE.
