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
   OpenSkyMount would need an `OnStepDriver` adapter that sends target RA/Dec or alt/az through LX200 commands
   instead of axis setpoints. This adapter is planned, not implemented.

Recommendation: start with OSM/1 on an ESP32 for the first bench test, because it maps one-to-one onto the
simulator that is already tested. Evaluate OnStepX in parallel if you want standard LX200 compatibility on the
device itself.

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
