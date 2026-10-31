"""Hardware abstraction: the axis-level mount driver interface.

The controller (``controller.py``) owns astronomy, safety policy and tracking and
talks to hardware exclusively through :class:`MountDriver`. Drivers speak in
*mount coordinates*: altitude-axis angle and multi-turn (unwrapped) azimuth-axis
angle, in degrees, as measured by the mount's own position reference.

Implementations:
* ``SimulatedMountDriver``  – in-process physics simulation (default)
* ``ProtocolMountDriver``   – OSM line protocol over TCP/serial (ESP32 firmware
                               or the bundled fake-firmware endpoint)

A future OnStepX/LX200 or INDI/Alpaca adapter would implement the same class.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any


class Fault:
    """Fault codes reported by drivers."""

    LIMIT_ALT = "limit_alt"
    LIMIT_AZ = "limit_az"
    STALL_ALT = "stall_alt"  # following error: motor commanded but axis did not move
    STALL_AZ = "stall_az"
    OVERCURRENT = "overcurrent"
    SENSOR_DISAGREEMENT = "sensor_disagreement"  # step count vs axis encoder mismatch
    HOMING_FAILED = "homing_failed"
    CONNECTION_LOST = "connection_lost"
    POSITION_UNKNOWN = "position_unknown"  # not homed / reference lost
    ESTOP = "estop"


@dataclass
class Capabilities:
    name: str
    kind: str  # simulated | protocol | ...
    simulated: bool
    mount_type: str = "altaz"
    axes: int = 2
    supports_time_warp: bool = False  # physics follow simulation time (sims only)
    supports_rate_feedforward: bool = True
    has_axis_encoders: bool = False
    has_homing_sensors: bool = False
    has_hardware_estop: bool = False
    max_rate: float = 5.0
    max_accel: float = 5.0
    alt_min: float = -5.0
    alt_max: float = 90.0
    az_min: float = -270.0
    az_max: float = 270.0
    firmware: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AxisStatus:
    position: float = 0.0  # step-count / commanded-reference position (deg)
    velocity: float = 0.0  # deg/s
    encoder: float | None = None  # independent axis encoder reading, if fitted
    moving: bool = False
    at_limit: bool = False


@dataclass
class DriverStatus:
    connected: bool
    alt: AxisStatus
    az: AxisStatus
    homed: bool
    homing: bool = False
    estop: bool = False
    faults: list[str] = field(default_factory=list)
    timestamp: float = 0.0  # simulation time of this status
    # Ground truth: only simulated drivers can know where the payload really points.
    truth: dict[str, float] | None = None
    motor: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MountDriver(ABC):
    @abstractmethod
    def capabilities(self) -> Capabilities: ...

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def status(self) -> DriverStatus: ...

    @abstractmethod
    def move_to(self, alt: float, az: float, alt_rate: float = 0.0, az_rate: float = 0.0) -> None:
        """Position setpoint (mount coordinates) with optional velocity feed-forward."""

    @abstractmethod
    def set_rates(self, alt_rate: float, az_rate: float) -> None:
        """Velocity mode (manual slewing)."""

    @abstractmethod
    def stop(self) -> None:
        """Controlled deceleration to rest."""

    @abstractmethod
    def emergency_stop(self) -> None:
        """Immediate stop; latched until :meth:`reset`. Not a substitute for a hardware E-stop."""

    @abstractmethod
    def reset(self) -> None:
        """Clear latched faults / E-stop after the cause has been removed."""

    @abstractmethod
    def home(self) -> None:
        """Run the homing sequence (establish the position reference)."""

    @abstractmethod
    def sync_position(self, alt: float, az: float) -> None:
        """Redefine the current position reference (calibration)."""

    @abstractmethod
    def update(self, sim_time: float) -> None:
        """Advance simulation / poll hardware. Called regularly by the controller."""

    # Optional: fault injection for simulators.
    def inject_fault(self, fault: str, active: bool = True) -> None:
        raise NotImplementedError("fault injection is only available on simulated drivers")
