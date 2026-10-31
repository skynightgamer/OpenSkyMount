# Simulation guide

The simulated mount (`mount/sim_driver.py`, `mount/axis.py`) models plausible mechanical behaviour. It does not
represent any specific motor.

## What is modelled
- **Per-axis motion**: velocity and acceleration limits; position control with rate feed-forward
  (`target += rate·dt` between setpoints); a discrete-time braking law that does not overshoot; velocity mode for
  manual slews; controlled stop and instant E-stop.
- **Backlash**: a dead band between the motor and the output shaft. The controller compensates with the configured
  value, approaching from the direction of motion.
- **Missed steps (stall)**: the step counter keeps counting while the shaft stays still. With axis encoders this is
  detected as `sensor_disagreement`.
- **Latency**: commands take effect after `command_latency_s`. E-stop is modelled as an immediate dedicated line.
- **Pointing errors**: azimuth and altitude zero-point errors and base tilt change where the payload *really*
  points. The controller cannot see them until you use Sync or plate solving. The UI shows both the mount-reported
  error and the true error (simulation ground truth).
- **Limits**: firmware soft limits, hard limit switches 2° beyond them, and the cable-wrap range.
- **Faults** (Settings → fault injection): `stall_alt`, `stall_az`, `overcurrent`, `encoder_glitch`, `disconnect`,
  `homing_failure`.

## Time acceleration
The clock panel sets live time, a simulated date and time, or a rate of 1–600× (API up to 3600×). Physics and
astronomy both follow simulation time, so 600× compresses an hour of tracking into six seconds. Dead-man timers
use real time.

## Things to try
1. **Tracking a planet**: GoTo Jupiter at ×600 and watch the error stay at a few arcseconds while field rotation
   changes.
2. **Zenith keyhole**: pick a star whose declination is close to your latitude, then track it through the meridian.
   Near the zenith the azimuth rate spikes, the flag `rate_limited` appears, and the error recovers after transit.
3. **Cable wrap**: set `limits.az_min/max` to ±180 and track a southern star through the meridian to see the
   automatic unwind.
4. **Alignment**: set `az_zero_offset = 0.8`, GoTo Capella, and note the true error of about 30′. Press
   "Align: plate solve" and the error drops to under 1′.
5. **Faults**: inject `stall_az` during a slew and the mount stops with a fault. Reset (encoders re-reference) and
   Home.
6. **Multi-device**: open the UI on two devices. Take control on one; the other shows the same state, and its
   E-STOP still works.

## Determinism
Given the same configuration (`sim.seed`) and the same sequence of `update(sim_time)` calls, results are
bit-identical (`test_determinism`). The tests drive the controller directly through `tests/conftest.py::Rig`.
