"""MountDriver for LX200-protocol controllers: OnStepX (and OnStep) in alt-az (ALTAZM) mode.

The controller (``controller.py``) speaks in axis setpoints with velocity feed-forward at 20 Hz; LX200 offers a GoTo
to an alt/az target, timed-free directional moves at a set rate, position getters and stop. This adapter translates:

* **Far from the setpoint** (> ``goto_threshold``): a firmware GoTo (``:Sz``/``:Sa``/``:MA#``). Long azimuth moves are
  split into legs of at most 150° so the firmware's shortest path is the direction OpenSkyMount chose (cable wrap).
* **Near the setpoint** (tracking, fine corrections): a rate loop. Each axis moves at feed-forward + error / tau via
  ``:RA``/``:RE`` (rate in degrees per second) and ``:Me/:Mw/:Mn/:Ms``; ``:Q?#`` stops an axis.
* **Manual slews**: the same directional moves at the requested rates.

The firmware's own tracking is switched off at connect (``:Td#``): OpenSkyMount owns the motion, as with every
driver. Positions (``:GA#``/``:GZ#``) are polled by a worker thread; azimuth is unwrapped into multi-turn mount
coordinates. ``sync_position`` keeps an offset in this adapter and never changes the firmware's model.

Safety notes (see docs/hardware.md): LX200 has no host watchdog, so a directional move continues if the link drops.
The adapter sends ``:Q#`` on stop, E-stop, disconnect and when the controller stops calling ``update()`` for
``host_watchdog_s``, and faults with ``direction_mismatch`` (and stops) if an axis moves against the commanded
direction. The E-stop here is software only, like on every driver; real hardware needs a physical one.

Not verified against real OnStepX firmware: tested against ``fake_onstep.py``, which implements the same command
subset as this file understands it.
"""

from __future__ import annotations

import contextlib
import math
import socket
import threading
import time
from typing import Any

from ..config import MountLimits
from .driver import AxisStatus, Capabilities, DriverStatus, Fault, MountDriver
from .lx200 import (
    ACK,
    ALT_MINUS,
    ALT_PLUS,
    AZ_MINUS,
    AZ_PLUS,
    GOTO_ERRORS,
    LX200Error,
    format_alt,
    format_az,
    format_rate,
    has_seconds,
    parse_angle,
)

DEFAULT_TCP_PORT = 9999  # OnStepX Wi-Fi command channel
SCHEMES = ("onstep://", "lx200://", "onstep+serial://", "lx200+serial://")


def wrap180(a: float) -> float:
    return (a + 180.0) % 360.0 - 180.0


class _Link:
    """Request/response over a byte stream. One reader (the worker); writes are serialised by a lock."""

    def __init__(self, stream: Any) -> None:
        self.stream = stream
        self.buf = b""
        self.wlock = threading.Lock()

    def send(self, data: bytes) -> None:
        with self.wlock:
            self.stream.sendall(data)

    def _fill(self) -> None:
        chunk = self.stream.recv(256)
        if not chunk:
            raise ConnectionError("connection closed by the controller")
        self.buf += chunk

    def char(self, cmd: bytes) -> str:
        self.buf = b""
        self.send(cmd)
        while not self.buf:
            self._fill()
        c, self.buf = self.buf[:1], self.buf[1:]
        return c.decode("ascii", "replace")

    def text(self, cmd: bytes) -> str:
        self.buf = b""
        self.send(cmd)
        while b"#" not in self.buf:
            self._fill()
            if len(self.buf) > 512:
                raise LX200Error("reply too long")
        line, _, self.buf = self.buf.partition(b"#")
        return line.decode("ascii", "replace")

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self.stream.close()


class OnStepMountDriver(MountDriver):
    def __init__(self, spec: str, limits: MountLimits, poll_s: float = 0.1, timeout_s: float = 1.0,
                 goto_threshold: float = 0.5, fine_tau: float = 0.5, fine_max: float = 1.0,
                 host_watchdog_s: float = 1.0) -> None:
        self.spec = spec
        self.limits = limits
        self.poll_s = poll_s
        self.timeout_s = timeout_s
        self.goto_threshold = goto_threshold
        self.fine_tau = fine_tau
        self.fine_max = fine_max
        self.host_watchdog_s = host_watchdog_s
        self._lock = threading.Lock()
        self._link: _Link | None = None
        self._worker: threading.Thread | None = None
        self._stop_worker = threading.Event()
        self._connected = False
        self._product = ""
        self._firmware = ""
        self._errors: list[str] = []
        self._sim_time = 0.0
        self._heartbeat = time.monotonic()
        # Commanded state (set by the controller thread, executed by the worker).
        self._want: tuple[Any, ...] = ("idle",)
        self._estop = False
        self._faults: set[str] = set()
        self._homing = False
        # Sync offsets: mount coordinates = firmware reading + offset (azimuth unwrapped).
        self._off_alt = 0.0
        self._off_az = 0.0
        # Latest reading (firmware frame, azimuth unwrapped) and velocity estimate.
        self._alt = 0.0
        self._uaz = 0.0
        self._raw_az = 0.0
        self._v_alt = 0.0
        self._v_az = 0.0
        self._read_at = 0.0
        self._slewing = False  # firmware GoTo in progress (from :GU#)
        self._flags = ""
        self._rtt = 0.0
        # Worker-side motion bookkeeping.
        self._goto_dest: tuple[float, float] | None = None  # firmware frame, unwrapped azimuth
        self._goto_since = 0.0
        self._goto_ref: tuple[float, float, float, float, float] = (0.0, 0.0, 0.0, 0.0, 0.0)  # setpoint at GoTo start
        self._goto_err0 = 0.0  # azimuth error when the GoTo started
        self._sent = {"alt": 0.0, "az": 0.0}  # signed rates currently commanded per axis
        self._sent_at = {"alt": 0.0, "az": 0.0}
        self._last_q = 0.0

    # ------------------------------------------------------------ transport
    def _open(self) -> Any:
        if self.spec.startswith(("onstep://", "lx200://")):
            hostport = self.spec.split("://", 1)[1].rstrip("/")
            host, _, port = hostport.rpartition(":") if ":" in hostport else (hostport, "", "")
            s = socket.create_connection((host.strip("[]"), int(port or DEFAULT_TCP_PORT)), timeout=3.0)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            s.settimeout(self.timeout_s)
            return s
        if self.spec.startswith(("onstep+serial://", "lx200+serial://")):  # pragma: no cover - needs hardware
            try:
                import serial  # type: ignore[import-not-found]
            except ImportError as exc:
                raise ConnectionError("serial support needs 'pip install pyserial'") from exc
            path, _, baud = self.spec.split("://", 1)[1].partition("@")
            return _SerialStream(serial.serial_for_url(path, baudrate=int(baud or 9600), timeout=self.timeout_s))
        raise ConnectionError(f"unsupported driver spec {self.spec!r}")

    # ------------------------------------------------------------ interface
    def connect(self) -> None:
        self.disconnect()
        link = _Link(self._open())
        try:
            mode = link.char(ACK)
            if mode in ("P", "G"):
                raise ConnectionError("the controller is set up as an equatorial mount; OpenSkyMount needs alt-az (ALTAZM)")
            if mode not in ("A", "L"):
                raise ConnectionError(f"unexpected answer to ACK: {mode!r}")
            self._product = link.text(b":GVP#")
            self._firmware = link.text(b":GVN#")
            alt_text = link.text(b":GA#")
            if not has_seconds(alt_text):
                link.send(b":U#")  # toggle to high precision (arc-seconds)
                alt_text = link.text(b":GA#")
                if not has_seconds(alt_text):
                    raise ConnectionError("the controller reports positions to 1 arc-minute only")
            link.char(b":Td#")  # firmware tracking off: OpenSkyMount drives both axes
            link.send(b":Q#")
        except (OSError, LX200Error) as exc:
            link.close()
            raise ConnectionError(f"LX200 handshake failed: {exc}") from exc
        except ConnectionError:
            link.close()
            raise
        with self._lock:
            self._link = link
            self._want = ("idle",)
            self._sent = {"alt": 0.0, "az": 0.0}
            self._goto_dest = None
            self._heartbeat = time.monotonic()
        self._poll(link, first=True)
        self._connected = True
        self._stop_worker.clear()
        self._worker = threading.Thread(target=self._run, args=(link,), daemon=True, name="onstep-driver")
        self._worker.start()

    def disconnect(self) -> None:
        self._stop_worker.set()
        if self._worker is not None and self._worker is not threading.current_thread():
            self._worker.join(timeout=self.timeout_s + 1.0)  # finish the poll in progress first
        self._worker = None
        link, self._link = self._link, None
        if link is not None:
            with contextlib.suppress(OSError):
                link.send(b":Q#")  # never leave the mount moving: the last command sent
            link.close()
        self._connected = False

    def capabilities(self) -> Capabilities:
        lim = self.limits
        name = self._product or "LX200 controller"
        return Capabilities(
            name=f"{name} (LX200)", kind="lx200", simulated=False, supports_time_warp=False, has_axis_encoders=False,
            has_homing_sensors=False, max_rate=lim.max_rate, max_accel=lim.max_accel, alt_min=lim.min_altitude,
            alt_max=lim.max_altitude, az_min=lim.az_min, az_max=lim.az_max, firmware=self._firmware,
        )

    def status(self) -> DriverStatus:
        now = time.monotonic()
        with self._lock:
            fresh = self._connected and now - self._read_at < max(1.5, 4 * self.poll_s)
            faults = sorted(self._faults)
            if not fresh:
                faults.append(Fault.CONNECTION_LOST)
            if self._estop:
                faults.append(Fault.ESTOP)
            age = now - self._read_at
            alt = self._alt + self._off_alt
            az = self._uaz + self._off_az
            v_alt, v_az = self._v_alt, self._v_az
            moving_alt = abs(v_alt) > 2e-4 or self._sent["alt"] != 0.0 or self._slewing
            moving_az = abs(v_az) > 2e-4 or self._sent["az"] != 0.0 or self._slewing
            return DriverStatus(
                connected=fresh,
                alt=AxisStatus(position=alt, velocity=v_alt, moving=moving_alt),
                az=AxisStatus(position=az, velocity=v_az, moving=moving_az),
                homed=self._read_at > 0,  # OnStep keeps the position it was started (or homed) with
                homing=self._homing,
                estop=self._estop,
                faults=faults,
                timestamp=self._sim_time,
                truth=None,
                motor={"link": self.spec, "firmware": f"{self._product} {self._firmware}".strip(),
                       "status_age_s": round(age, 3), "rtt_ms": round(self._rtt * 1000, 1), "flags": self._flags,
                       "errors": self._errors[-3:]},
            )

    def move_to(self, alt: float, az: float, alt_rate: float = 0.0, az_rate: float = 0.0) -> None:
        with self._lock:
            if not self._estop:
                self._want = ("pos", alt, az, alt_rate, az_rate, time.monotonic())

    def set_rates(self, alt_rate: float, az_rate: float) -> None:
        with self._lock:
            if not self._estop:
                self._want = ("rates", alt_rate, az_rate)

    def stop(self) -> None:
        with self._lock:
            self._want = ("stop",)
            self._homing = False

    def emergency_stop(self) -> None:
        with self._lock:
            self._estop = True
            self._want = ("stop",)
            self._homing = False
            link = self._link
        if link is not None:  # at once, from the caller's thread (:Q# has no reply, so it cannot desync the worker)
            with contextlib.suppress(OSError):
                link.send(b":Q#")
                self._last_q = time.monotonic()

    def reset(self) -> None:
        with self._lock:
            self._estop = False
            self._faults.clear()
            self._want = ("idle",)

    def home(self) -> None:
        with self._lock:
            self._want = ("home",)
            self._homing = True

    def sync_position(self, alt: float, az: float) -> None:
        with self._lock:
            self._off_alt = alt - self._alt
            self._off_az = az - self._uaz

    def update(self, sim_time: float) -> None:
        self._sim_time = sim_time
        self._heartbeat = time.monotonic()

    # ------------------------------------------------------------ worker
    def _run(self, link: _Link) -> None:
        while not self._stop_worker.is_set():
            t0 = time.monotonic()
            try:
                self._poll(link)
                self._act(link)
            except (OSError, LX200Error, ConnectionError) as exc:
                self._error(f"link: {exc}")
                with self._lock:
                    self._connected = False
                    self._sent = {"alt": 0.0, "az": 0.0}
                link.close()
                return
            self._stop_worker.wait(max(0.0, self.poll_s - (time.monotonic() - t0)))

    def _error(self, msg: str) -> None:
        self._errors = (self._errors + [msg])[-10:]

    def _poll(self, link: _Link, first: bool = False) -> None:
        t_send = time.monotonic()
        alt = parse_angle(link.text(b":GA#"))
        t = time.monotonic()
        az = parse_angle(link.text(b":GZ#")) % 360.0
        flags = link.text(b":GU#")
        with self._lock:
            self._rtt = (t - t_send) if self._rtt == 0 else 0.8 * self._rtt + 0.2 * (t - t_send)
            if first or self._read_at == 0:
                uaz = az if az <= 180.0 else az - 360.0
                self._v_alt = self._v_az = 0.0
            else:
                uaz = self._uaz + wrap180(az - self._raw_az)
                dt = t - self._read_at
                if dt > 1e-3:
                    k = 0.6
                    self._v_alt = (1 - k) * self._v_alt + k * (alt - self._alt) / dt
                    self._v_az = (1 - k) * self._v_az + k * (uaz - self._uaz) / dt
            self._alt, self._raw_az, self._uaz, self._read_at = alt, az, uaz, t
            self._flags = flags
            self._slewing = "N" not in flags  # OnStep :GU# 'N' = no GoTo in progress
            if self._homing and "H" in flags and not self._slewing:
                self._homing = False

    def _act(self, link: _Link) -> None:
        now = time.monotonic()
        with self._lock:
            want = self._want
            estop = self._estop
            if want[0] == "stop":
                self._want = ("idle",)
        moving = self._slewing or any(self._sent.values()) or abs(self._v_alt) > 1e-3 or abs(self._v_az) > 1e-3
        if estop:
            if moving and now - self._last_q > 0.3:
                self._halt(link)
            return
        if want[0] in ("pos", "rates") and now - self._heartbeat > self.host_watchdog_s:
            # The controller loop stopped calling update(): stop instead of moving on with stale commands.
            self._error("controller silent: motion stopped")
            with self._lock:
                self._want = ("idle",)
            if moving:
                self._halt(link)
            return
        self._check_direction(link, now)
        if want[0] == "stop":
            if moving:
                self._halt(link)
        elif want[0] == "home":
            with self._lock:
                self._want = ("idle",)
            self._halt(link)
            link.send(b":hC#")
            self._goto_dest = (0.0, 0.0)
            self._goto_since = now
            self._goto_ref = (self._alt, self._uaz, 0.0, 0.0, now)
            self._goto_err0 = 360.0
        elif want[0] == "rates":
            if self._goto_dest is not None:
                self._halt(link)
            self._axis(link, "alt", want[1], now)
            self._axis(link, "az", want[2], now)
        elif want[0] == "pos":
            self._track(link, want, now)

    def _halt(self, link: _Link) -> None:
        link.send(b":Q#")
        self._last_q = time.monotonic()
        self._goto_dest = None
        self._sent = {"alt": 0.0, "az": 0.0}

    def _track(self, link: _Link, want: tuple[Any, ...], now: float) -> None:
        _, alt, az, ar, zr, t_set = want
        # Setpoint now (it refers to when it was sent) in the firmware frame; position extrapolated to now.
        lead = now - t_set + self._rtt / 2
        sp_alt = alt + ar * lead - self._off_alt
        sp_az = az + zr * lead - self._off_az
        age = now - self._read_at
        e_alt = sp_alt - (self._alt + self._v_alt * age)
        e_az = sp_az - (self._uaz + self._v_az * age)
        if self._goto_dest is not None:
            if self._slewing or now - self._goto_since < 0.4:
                # Let the firmware GoTo finish unless the setpoint left the path it had when the GoTo started
                # (a different target was chosen): then stop, and the next poll starts a new GoTo.
                r_alt, r_az, r_ar, r_zr, r_t = self._goto_ref
                if math.hypot(sp_alt - (r_alt + r_ar * (now - r_t)), sp_az - (r_az + r_zr * (now - r_t))) > 2.0:
                    self._halt(link)
                return
            self._goto_dest = None
            if abs(e_az) > self._goto_err0 + 1.0:
                # The firmware went the other way round (its own azimuth limits differ from OpenSkyMount's cable
                # wrap). Repeating the GoTo would loop, and the cable wrap is no longer what the controller thinks.
                self._fault(Fault.GOTO_REJECTED, "the controller turned the other way round in azimuth; "
                            "set its azimuth limits at least as wide as OpenSkyMount's cable-wrap limits")
                return
        if max(abs(e_alt), abs(e_az)) > self.goto_threshold:
            if any(self._sent.values()):
                self._halt(link)
                return  # issue the GoTo once the axes have stopped (next poll)
            if abs(self._v_alt) > 0.05 or abs(self._v_az) > 0.05:
                return
            # Lead a moving target by the expected slew time; split long azimuth moves into legs <= 150°.
            eta = max(abs(e_alt), abs(e_az)) / max(0.1, self.limits.max_rate) + 1.5
            d_alt = sp_alt + ar * eta
            d_az = sp_az + zr * eta
            if abs(d_az - self._uaz) > 170.0:
                d_az = self._uaz + math.copysign(150.0, d_az - self._uaz)
            self._goto_ref = (sp_alt, sp_az, ar, zr, now)
            self._goto_err0 = abs(e_az)
            self._goto(link, d_alt, d_az, now)
            return
        for axis, e, ff in (("alt", e_alt, ar), ("az", e_az, zr)):
            r = ff + e / self.fine_tau
            r = max(-self.fine_max, min(self.fine_max, r))
            if abs(e) < 1.0 / 3600 and abs(ff) < 1e-7:
                r = 0.0
            self._axis(link, axis, r, now)

    def _goto(self, link: _Link, alt: float, uaz: float, now: float) -> None:
        alt = max(-90.0, min(90.0, alt))
        ok_z = link.char(f":Sz{format_az(uaz)}#".encode())
        ok_a = link.char(f":Sa{format_alt(alt)}#".encode())
        if ok_z != "1" or ok_a != "1":
            self._fault(Fault.LIMIT_AZ if ok_z != "1" else Fault.LIMIT_ALT, f"target rejected (:Sz {ok_z}, :Sa {ok_a})")
            return
        reply = link.char(b":MA#")
        if reply != "0":
            why = GOTO_ERRORS.get(reply, f"error {reply!r}")
            fault = {"1": Fault.LIMIT_ALT, "2": Fault.LIMIT_ALT, "6": Fault.LIMIT_AZ}.get(reply, Fault.GOTO_REJECTED)
            self._fault(fault, f"GoTo refused: {why}")
            return
        self._goto_dest = (alt, uaz)
        self._goto_since = now

    def _fault(self, fault: str, msg: str) -> None:
        self._error(msg)
        with self._lock:
            self._faults.add(fault)
            self._want = ("idle",)

    def _axis(self, link: _Link, axis: str, rate: float, now: float) -> None:
        plus, minus = (ALT_PLUS, ALT_MINUS) if axis == "alt" else (AZ_PLUS, AZ_MINUS)
        last = self._sent[axis]
        rate = max(-self.limits.max_rate, min(self.limits.max_rate, rate))
        if rate == 0.0:
            if last != 0.0:
                link.send(f":Q{plus if last > 0 else minus}#".encode())
                self._sent[axis] = 0.0
                self._sent_at[axis] = now
            return
        changed = last == 0.0 or (rate > 0) != (last > 0) or abs(rate - last) > 0.03 * abs(last) + 2e-5
        if not changed:
            return
        if last != 0.0 and (rate > 0) != (last > 0):
            link.send(f":Q{plus if last > 0 else minus}#".encode())
        link.send(f":R{'E' if axis == 'alt' else 'A'}{format_rate(rate)}#".encode())
        link.send(f":M{plus if rate > 0 else minus}#".encode())
        if last == 0.0 or (rate > 0) != (last > 0):
            self._sent_at[axis] = now
        self._sent[axis] = rate

    def _check_direction(self, link: _Link, now: float) -> None:
        """Fault if an axis runs against the commanded direction (reversed wiring or direction mapping)."""
        for axis, v in (("alt", self._v_alt), ("az", self._v_az)):
            r = self._sent[axis]
            if abs(r) < 0.05 or now - self._sent_at[axis] < 0.6:
                continue
            if v * r < 0 and abs(v) > 0.3 * abs(r):
                self._halt(link)
                self._fault(Fault.DIRECTION_MISMATCH, f"{axis} axis moves against the commanded direction; stopped")
                return


class _SerialStream:  # pragma: no cover - needs hardware
    def __init__(self, port: Any) -> None:
        self.port = port

    def sendall(self, data: bytes) -> None:
        self.port.write(data)

    def recv(self, n: int) -> bytes:
        data = self.port.read(1) or b""
        if not data:
            raise TimeoutError("no reply from the controller")
        return data + self.port.read(min(n - 1, self.port.in_waiting or 0))

    def close(self) -> None:
        self.port.close()
