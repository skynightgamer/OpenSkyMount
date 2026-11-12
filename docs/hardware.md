# Hardware roadmap (preliminary, nothing built or tested)

This is a design-level plan. Component *categories* and selection criteria are given instead of specific part
promises. Validate every choice on a bench before trusting it with a payload.

## Reference architecture

```
 Host (laptop / Raspberry Pi running OpenSkyMount server)
        │ USB-serial or Wi-Fi TCP  (OSM/1 protocol, or LX200 via OnStepX)
 ┌──────▼─────────────────────────────┐
 │ ESP32 (dual core, Wi-Fi)           │── home switch ALT, home switch AZ (NC)
 │  • step generation (FastAccelStepper / RMT)   ── limit switches (NC)
 │  • per-axis position+rate setpoints│── AS5600/AS5048 magnetic encoders (I²C/SPI) on output shafts
 │  • watchdog, limits, E-stop latch  │── supply voltage sense
 └──────┬──────────────┬──────────────┘
   STEP/DIR/EN    STEP/DIR/EN            Hardware E-stop switch cuts VMOT ─┐
 ┌──────▼─────┐ ┌──────▼─────┐                                             │
 │ TMC2209 ALT│ │ TMC2209 AZ │◀──── VMOT 12–24 V (fused) ◀─────────────────┘
 └──────┬─────┘ └──────┬─────┘
   NEMA17 + reduction   NEMA17 + reduction (worm, belt or planetary), tripod 3/8" interface
```

## Bill of materials (categories and criteria)

| Category | Selection criteria | Notes |
|---|---|---|
| Microcontroller | ESP32 class (dual core, Wi-Fi, ≥ 2 hardware timers/RMT), good library support | ESP32-S3 dev boards are common; OnStepX also supports ESP32 |
| Motors (2) | NEMA 17 bipolar steppers, 0.9° or 1.8°, torque sized for the payload (holding torque ≥ 0.4 N·m for ≤ 1 kg at the output after reduction) | Geared DC motors with encoders are an alternative but need closed-loop firmware |
| Reduction (2) | Total ratio 100:1 to 400:1 for a smooth sidereal rate (0.0042°/s) with microstepping; low or adjustable backlash; non-back-drivable worm preferred for altitude | Belt plus worm combinations are common in DIY mounts |
| Motor drivers (2) | Silent microstepping (≥ 1/16), programmable current limit, stall detection (e.g. TMC2209 via UART) | Current limit is a safety item |
| Axis encoders (2, recommended) | Absolute magnetic encoders on the output shafts (12–14 bit). They detect lost steps and enable `sensor_disagreement` | The simulator models this (`has_axis_encoders`) |
| Home/limit switches | Mechanical micro switches or Hall sensors, wired normally closed | One home switch per axis, hard limits for altitude and cable wrap |
| Power | 12 V (light payloads) or 24 V supply, fuse, reverse-polarity protection, 5 V buck for logic, battery option for field use | Size for both motors at full current plus 30 % |
| E-stop | Latching mushroom switch in series with VMOT | Required, see safety.md |
| Structure | Tripod with 3/8"-16 thread, azimuth bearing (lazy-susan or tapered roller), fork or L-bracket altitude axis, Arca-Swiss payload clamp | 3D-printed prototype, aluminium for final |
| Payload adapters | Smartphone clamp, 1/4"-20 camera screw, Arca plate for lenses, tube rings for telescopes | Matches the `PAYLOADS` profiles |
| Optional | Payload camera for plate solving (phone camera or a small USB camera), GPS for location and time, RTC | Plate-solving PoC in `backend/openskymount/solve` |

## Firmware options
1. **Custom minimal firmware implementing OSM/1** (see [firmware/README.md](../firmware/README.md)). It is a small
   surface: setpoints, rates, stop, E-stop, home, sync, status. The host already does all the astronomy and
   tracking math, and `fake_firmware.py` is the executable reference.
2. **OnStepX** (GPL-3.0, mature, supports alt-az and ESP32, speaks LX200). The firmware does its own astronomy.
   OpenSkyMount talks to it through the experimental `OnStepMountDriver` (`mount/onstep_driver.py`, D48):
   `--driver onstep://<host>[:9999]` (Wi-Fi command channel) or `--driver onstep+serial:///dev/ttyUSB0@9600`.
   **Tested only against `fake_onstep.py`, not against real OnStepX firmware** (see the checklist below).

Recommendation: start with OSM/1 on an ESP32 for the first bench test, because it maps one-to-one onto the
simulator that is already tested. Evaluate OnStepX in parallel if you want standard LX200 compatibility on the
device itself.

### OnStepX adapter: how it works and what to check on real firmware
The controller still owns astronomy, tracking and safety; the adapter maps its axis setpoints onto LX200:

| Controller asks for | Adapter sends |
|---|---|
| Position far from the current one (> 0.5°) | Firmware GoTo: `:Sz DDD*MM'SS#`, `:Sa sDD*MM'SS#`, `:MA#`. Azimuth moves over 170° go in legs of ≤ 150° so the firmware's shortest path is the direction OpenSkyMount chose |
| Position near the current one (tracking, fine moves) | Rate loop at 10 Hz: feed-forward + error / 0.5 s per axis via `:RA<deg/s>#` (azimuth) and `:RE<deg/s>#` (altitude) with `:Mw/:Me/:Mn/:Ms#`; `:Qe/:Qw/:Qn/:Qs#` stop one axis |
| Manual slew | The same directional moves at the requested rates |
| Stop, E-stop, disconnect | `:Q#` (E-stop is also latched in the adapter until Reset) |
| Home | `:hC#` (OnStep's home position) |
| Sync | An offset in the adapter; the firmware's model is not changed |

At connect it checks the mount mode (ACK must answer `A`; equatorial setups are refused), switches replies to
arc-second precision (`:U#`) if needed and turns the firmware's own tracking off (`:Td#`). Positions are polled with
`:GA#`, `:GZ#` and `:GU#` (10 Hz); azimuth is unwrapped into multi-turn mount coordinates.

**Safety limits of this path.** LX200 has no host watchdog: a directional move continues if the link drops or the
host dies. The adapter sends `:Q#` on stop, E-stop and disconnect and when the controller loop stops calling it for
1 s, faults (`direction_mismatch`) and stops if an axis moves against the commanded direction, and faults
(`goto_rejected`) if a GoTo is refused or the firmware turns the other way round in azimuth. A physical E-stop and
OnStep's own limits remain mandatory.

**To verify on real OnStepX (assumptions taken from the command reference, not measured):**
1. `:Mw#` increases azimuth and `:Mn#` raises the altitude on an alt-az OnStep (the adapter faults if not).
2. `:RA`/`:RE` accept small rates (sidereal ≈ 0.004°/s) and apply to the following `:M?#` moves with tracking off.
3. `:GA#`/`:GZ#` report the axis position without OnStep's pointing model and refraction (disable both in OnStep,
   or Sync will fight them).
4. `:MA#` reply codes and the `N` (no GoTo) flag in `:GU#`.
5. Set OnStep's azimuth limits at least as wide as OpenSkyMount's cable-wrap limits (default ±270°).

## Integration plan
1. Bench: one axis, stepper and driver, run the fake-firmware test suite (`tests/test_protocol.py`) against the real
   board over USB-serial (`--driver serial:///dev/ttyUSB0@115200`).
2. Add encoders and switches; verify homing, limits, watchdog and E-stop with the motor unloaded.
3. Two axes on the structure, no payload: GoTo and tracking. Measure backlash and enter it in the config.
4. Smartphone payload: compass/level-based rough alignment, then Sync on a bright star.
5. Camera plate solving to build a pointing model (multi-star), replacing the single offset.

## Calibration items to measure on real hardware
- Backlash per axis (`sim.backlash_*` doubles as the calibrated compensation value today).
- Steps per degree, maximum rate and acceleration under load.
- Encoder zero relative to the home switches.
- Base tilt and azimuth zero (a 3-star model is planned; one-star sync is available now).
