"""MountDriver that talks OSM/1 to a microcontroller over TCP (or serial via pyserial).

Commands are written without blocking; a reader thread parses replies. ``update``
polls STATUS at ``poll_hz``. If no status arrives for ``timeout_s`` the link is
reported lost (``connection_lost``) and the controller halts; the firmware's own
watchdog stops the motors on its side.
"""

from __future__ import annotations

import contextlib
import itertools
import socket
import threading
import time
from typing import Any

from ..config import MountLimits
from .driver import AxisStatus, Capabilities, DriverStatus, Fault, MountDriver
from .protocol import VERSION, ProtocolError, Request, Status, parse_hello


class ProtocolMountDriver(MountDriver):
    def __init__(self, spec: str, limits: MountLimits, poll_hz: float = 10.0, timeout_s: float = 1.5) -> None:
        self.spec = spec
        self.limits = limits
        self.poll_interval = 1.0 / poll_hz
        self.timeout_s = timeout_s
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._sock: Any = None
        self._reader: threading.Thread | None = None
        self._hello: dict[str, str] = {}
        self._status: Status | None = None
        self._status_at = 0.0
        self._last_poll = 0.0
        self._errors: list[str] = []
        self._connected = False
        self._sim_time = 0.0
        self._waiters: dict[int, tuple[threading.Event, list[str]]] = {}

    # ------------------------------------------------------------ transport
    def _open(self) -> Any:
        if self.spec.startswith("tcp://"):
            host, port = self.spec[6:].rsplit(":", 1)
            s = socket.create_connection((host, int(port)), timeout=3.0)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            s.settimeout(None)
            return s
        if self.spec.startswith("serial://"):
            try:
                import serial  # type: ignore[import-not-found]
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise ConnectionError("serial support needs 'pip install pyserial'") from exc
            path, _, baud = self.spec[9:].partition("@")
            return _SerialAdapter(serial.serial_for_url(path, baudrate=int(baud or 115200), timeout=0.5))
        raise ConnectionError(f"unsupported driver spec {self.spec!r}")

    def _send(self, cmd: str, *args: float) -> int:
        rid = next(self._ids)
        data = Request(rid, cmd, list(args)).encode()
        with self._lock:
            if self._sock is None:
                return rid
            try:
                self._sock.sendall(data)
            except OSError:
                self._connected = False
        return rid

    def _request(self, cmd: str, *args: float, timeout: float = 2.0) -> list[str]:
        ev = threading.Event()
        holder: list[str] = []
        rid = next(self._ids)
        self._waiters[rid] = (ev, holder)
        with self._lock:
            self._sock.sendall(Request(rid, cmd, list(args)).encode())
        if not ev.wait(timeout):
            self._waiters.pop(rid, None)
            raise ConnectionError(f"no reply to {cmd}")
        return holder

    def _read_loop(self, sock: Any) -> None:
        buf = b""
        try:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    self._on_line(line.decode("ascii", "replace").strip())
        except OSError:
            pass
        self._connected = False

    def _on_line(self, line: str) -> None:
        parts = line.split()
        if len(parts) < 2:
            return
        try:
            rid = int(parts[0])
        except ValueError:
            return
        kind = parts[1]
        if kind == "ST":
            try:
                self._status = Status.decode(parts[2:])
                self._status_at = time.monotonic()
            except (ProtocolError, ValueError):
                self._errors.append("malformed status")
        elif kind == "ERR":
            self._errors = (self._errors + [" ".join(parts[2:])])[-10:]
        waiter = self._waiters.pop(rid, None)
        if waiter:
            waiter[1].extend(parts[1:])
            waiter[0].set()

    # ------------------------------------------------------------ interface
    def connect(self) -> None:
        self.disconnect()
        self._sock = self._open()
        self._reader = threading.Thread(target=self._read_loop, args=(self._sock,), daemon=True)
        self._reader.start()
        reply = self._request("HELLO")
        if len(reply) < 2 or reply[0] != "OK" or reply[1] != VERSION:
            raise ConnectionError(f"unexpected HELLO reply: {' '.join(reply)}")
        self._hello = parse_hello(reply[2:])
        self._connected = True
        self._request("STATUS")

    def disconnect(self) -> None:
        if self._sock is not None:
            with contextlib.suppress(OSError):
                self._sock.close()
        self._sock = None
        self._connected = False

    def capabilities(self) -> Capabilities:
        h = self._hello

        def f(key: str, default: float) -> float:
            try:
                return float(h.get(key, default))
            except ValueError:
                return default

        return Capabilities(
            name=h.get("name", "OSM/1 controller"),
            kind="protocol",
            simulated=False,  # treated as real hardware by the safety policy
            supports_time_warp=False,
            has_axis_encoders=h.get("enc") == "1",
            has_homing_sensors=h.get("home") == "1",
            max_rate=f("vmax", self.limits.max_rate),
            max_accel=f("amax", self.limits.max_accel),
            alt_min=f("altmin", self.limits.min_altitude),
            alt_max=f("altmax", self.limits.max_altitude),
            az_min=f("azmin", self.limits.az_min),
            az_max=f("azmax", self.limits.az_max),
            firmware=h.get("fw", ""),
        )

    def status(self) -> DriverStatus:
        st = self._status
        fresh = st is not None and time.monotonic() - self._status_at < self.timeout_s
        link = self._connected and fresh
        if st is None:
            return DriverStatus(connected=False, alt=AxisStatus(), az=AxisStatus(), homed=False,
                                faults=[Fault.CONNECTION_LOST], timestamp=self._sim_time)
        faults = list(st.faults)
        if not link and Fault.CONNECTION_LOST not in faults:
            faults.append(Fault.CONNECTION_LOST)
        return DriverStatus(
            connected=link,
            alt=AxisStatus(position=st.alt, velocity=st.v_alt, encoder=st.enc_alt, moving=abs(st.v_alt) > 1e-6),
            az=AxisStatus(position=st.az, velocity=st.v_az, encoder=st.enc_az, moving=abs(st.v_az) > 1e-6),
            homed="H" in st.flags,
            homing="h" in st.flags,
            estop="E" in st.flags,
            faults=faults,
            timestamp=self._sim_time,
            truth=None,
            motor={"link": self.spec, "errors": self._errors[-3:], "status_age_s": round(time.monotonic() - self._status_at, 3)},
        )

    def move_to(self, alt: float, az: float, alt_rate: float = 0.0, az_rate: float = 0.0) -> None:
        self._send("MOVE", alt, az, alt_rate, az_rate)

    def set_rates(self, alt_rate: float, az_rate: float) -> None:
        self._send("RATE", alt_rate, az_rate)

    def stop(self) -> None:
        self._send("STOP")

    def emergency_stop(self) -> None:
        self._send("ESTOP")

    def reset(self) -> None:
        self._send("RESET")

    def home(self) -> None:
        self._send("HOME")

    def sync_position(self, alt: float, az: float) -> None:
        self._send("SYNC", alt, az)

    def update(self, sim_time: float) -> None:
        self._sim_time = sim_time
        now = time.monotonic()
        if self._sock is not None and now - self._last_poll >= self.poll_interval:
            self._last_poll = now
            self._send("STATUS")


class _SerialAdapter:  # pragma: no cover - needs hardware
    def __init__(self, port: Any) -> None:
        self.port = port

    def sendall(self, data: bytes) -> None:
        self.port.write(data)

    def recv(self, n: int) -> bytes:
        while True:
            data = self.port.read(n)
            if data:
                return data

    def close(self) -> None:
        self.port.close()

    def setsockopt(self, *a: Any) -> None:
        pass
