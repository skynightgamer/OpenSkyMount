"""Configuration model: observer site, payload profiles, mount limits, simulation parameters.

All angles are degrees, angular rates deg/s, accelerations deg/s^2.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any


@dataclass
class ObserverConfig:
    latitude: float = 52.52  # Berlin by default; any site can be entered
    longitude: float = 13.405
    elevation_m: float = 35.0
    name: str = "Berlin"
    # Atmospheric refraction applied to apparent altitudes (None disables it).
    temperature_c: float | None = 10.0
    pressure_mbar: float | None = 1010.0

    def validate(self) -> None:
        if not -90.0 <= self.latitude <= 90.0:
            raise ValueError("latitude must be within [-90, 90]")
        if not -180.0 <= self.longitude <= 180.0:
            raise ValueError("longitude must be within [-180, 180]")
        if not -500.0 <= self.elevation_m <= 9000.0:
            raise ValueError("elevation must be within [-500, 9000] m")


@dataclass
class PayloadProfile:
    """Design-level description of a payload.

    ``inertia_factor`` scales the axis acceleration available from the motors
    (heavier/longer payloads accelerate more slowly). ``max_altitude`` models
    payload/base collisions near the zenith for long optics.
    """

    id: str
    name: str
    mass_kg: float
    length_m: float
    inertia_factor: float
    max_altitude: float
    shape: str  # used by the 3D view: phone | camera | dslr | telephoto | telescope
    fov_deg: float  # typical field of view, used for pointing tolerance guidance


PAYLOADS: dict[str, PayloadProfile] = {
    p.id: p
    for p in [
        PayloadProfile("smartphone", "Smartphone", 0.2, 0.16, 1.0, 90.0, "phone", 60.0),
        PayloadProfile("compact", "Compact camera", 0.4, 0.12, 1.2, 90.0, "camera", 40.0),
        PayloadProfile("dslr", "DSLR / mirrorless + standard lens", 1.2, 0.20, 2.0, 88.0, "dslr", 30.0),
        PayloadProfile("telephoto", "Camera + telephoto lens", 2.5, 0.35, 3.5, 85.0, "telephoto", 5.0),
        PayloadProfile("telescope", "Small telescope (≤ 80 mm)", 3.0, 0.45, 4.0, 82.0, "telescope", 1.0),
    ]
}


@dataclass
class MountLimits:
    min_altitude: float = -5.0  # mechanical lower limit of the altitude axis
    max_altitude: float = 90.0  # mechanical upper limit (payload may reduce it)
    # Azimuth is multi-turn; total travel limited to protect cables.
    az_min: float = -270.0
    az_max: float = 270.0
    max_rate: float = 6.0  # deg/s, slewing speed for a light payload
    max_accel: float = 6.0  # deg/s^2 for a light payload
    tracking_max_rate: float = 6.0


@dataclass
class SimulationConfig:
    """Imperfections of the simulated mount. Defaults model a decent DIY mount."""

    seed: int = 42
    command_latency_s: float = 0.05
    backlash_alt: float = 0.05
    backlash_az: float = 0.08
    # Static pointing-model errors (what a plate solver would later measure).
    az_zero_offset: float = 0.0
    alt_zero_offset: float = 0.0
    tilt_magnitude: float = 0.0  # base not level
    tilt_direction: float = 0.0  # azimuth towards which the base dips
    encoder_noise: float = 0.002  # deg RMS reported-position noise
    has_axis_encoders: bool = True  # absolute encoders on the output shafts
    physics_step_s: float = 0.02


@dataclass
class ControlConfig:
    goto_tolerance_deg: float = 0.02
    tracking_tolerance_deg: float = 0.02
    min_target_altitude: float = 0.0  # targets below this are not reachable
    allow_solar_targets: bool = False  # never enabled by default; see docs/safety.md
    sun_exclusion_deg: float = 15.0
    track_after_goto: bool = True
    backlash_compensation: bool = True
    manual_deadman_s: float = 1.0  # manual slews stop without refresh
    auto_unwind: bool = True  # unwind the azimuth cable wrap automatically when tracking hits it
    control_lease_s: float = 15.0  # controller authority expires without heartbeat


@dataclass
class AppConfig:
    observer: ObserverConfig = field(default_factory=ObserverConfig)
    payload: str = "smartphone"
    limits: MountLimits = field(default_factory=MountLimits)
    sim: SimulationConfig = field(default_factory=SimulationConfig)
    control: ControlConfig = field(default_factory=ControlConfig)

    def payload_profile(self) -> PayloadProfile:
        return PAYLOADS[self.payload]

    def effective_limits(self) -> MountLimits:
        """Limits after applying the payload profile."""
        p = self.payload_profile()
        lim = MountLimits(**asdict(self.limits))
        lim.max_altitude = min(lim.max_altitude, p.max_altitude)
        lim.max_accel = lim.max_accel / p.inertia_factor
        lim.max_rate = lim.max_rate / max(1.0, p.inertia_factor**0.5)
        return lim

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AppConfig:
        cfg = cls()
        for name, sub in (("observer", cfg.observer), ("limits", cfg.limits), ("sim", cfg.sim), ("control", cfg.control)):
            _update_dataclass(sub, d.get(name, {}))
        if "payload" in d:
            if d["payload"] not in PAYLOADS:
                raise ValueError(f"unknown payload {d['payload']!r}")
            cfg.payload = d["payload"]
        cfg.observer.validate()
        return cfg

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load(cls, path: Path) -> AppConfig:
        if not path.exists():
            return cls()
        return cls.from_dict(json.loads(path.read_text()))


def _update_dataclass(obj: Any, values: dict[str, Any]) -> None:
    known = {f.name: f for f in fields(obj)}
    for k, v in values.items():
        if k not in known:
            raise ValueError(f"unknown setting {type(obj).__name__}.{k}")
        current = getattr(obj, k)
        if isinstance(current, bool):
            if not isinstance(v, bool):
                raise ValueError(f"{k} must be a boolean")
        elif isinstance(current, (int, float)) and not isinstance(current, bool):
            if v is None and k in ("temperature_c", "pressure_mbar"):
                pass
            elif not isinstance(v, (int, float)) or isinstance(v, bool):
                raise ValueError(f"{k} must be a number")
            else:
                v = type(current)(v) if isinstance(current, float) else v
        elif current is None and k in ("temperature_c", "pressure_mbar"):
            if v is not None and not isinstance(v, (int, float)):
                raise ValueError(f"{k} must be a number or null")
        elif isinstance(current, str) and not isinstance(v, str):
            raise ValueError(f"{k} must be a string")
        setattr(obj, k, v)
