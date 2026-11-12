"""Fake OnStep: the LX200 command subset of ``lx200.py`` over TCP, in front of the axis simulator, in real time.

Stands in for an OnStepX controller in alt-az mode so ``OnStepMountDriver`` and the whole network path can be tested
without hardware:

    openskymount-fake-onstep --port 9999
    python -m openskymount.server --driver onstep://127.0.0.1:9999

It models what the adapter relies on (GoTo with OnStep's error codes, directional moves at a set rate, stop, home,
position and status getters, precision toggle, ACK) and nothing else: no firmware tracking, no pointing model, no
parking. Options let tests simulate a mount set up as equatorial, low-precision replies and reversed azimuth wiring.
It is a test double written from the command reference, not a copy of OnStepX's behaviour.
"""

from __future__ import annotations

import argparse
import contextlib
import math
import socket
import socketserver
import threading
import time

from ..config import MountLimits, SimulationConfig
from .lx200 import ALT_MINUS, ALT_PLUS, AZ_MINUS, AZ_PLUS, LX200Error, format_alt, format_az, parse_angle
from .sim_driver import SimulatedMountDriver


def _low_precision(text: str) -> str:
    """``+DD*MM'SS`` -> ``+DD*MM`` (what OnStep sends before :U# switches to high precision)."""
    return text.split("'")[0]


class FakeOnStep:
    def __init__(self, limits: MountLimits | None = None, sim: SimulationConfig | None = None, clock=time.monotonic,
                 mode: str = "A", high_precision: bool = True, reverse_az: bool = False) -> None:
        self.limits = limits or MountLimits()
        self.sim = sim or SimulationConfig(command_latency_s=0.0, backlash_alt=0.0, backlash_az=0.0, encoder_noise=0.0)
        self.clock = clock
        self.mode = mode
        self.high_precision = high_precision
        self.reverse_az = reverse_az
        self.lock = threading.Lock()
        self.mount = SimulatedMountDriver(self.limits, self.sim, start_time=clock())
        self.mount.connect()
        for axis in (self.mount.alt, self.mount.az):
            axis.p.kp = 12.0  # stepper firmware ends a GoTo crisply; the default 3/s approach would crawl for seconds
        self.rate = {"alt": 1.0, "az": 1.0}  # deg/s set by :RE / :RA
        self.dir = {"alt": 0, "az": 0}  # -1, 0, +1 (in mount axis terms, after any wiring reversal)
        self.goto_dest: tuple[float, float] | None = None
        self.target_alt: float | None = None
        self.target_az: float | None = None
        self.commands: list[str] = []  # everything received (for tests)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._clients: list[socket.socket] = []

    def start(self) -> None:
        self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()

    def drop_connections(self) -> None:
        """Simulate a Wi-Fi drop: close every client socket (motion is NOT stopped, as on real LX200 hardware)."""
        for c in list(self._clients):
            with contextlib.suppress(OSError):
                c.shutdown(socket.SHUT_RDWR)
                c.close()

    def _run(self) -> None:
        while not self._stop.is_set():
            with self.lock:
                self.mount.update(self.clock())
                if self.goto_dest is not None:
                    # A GoTo ends once the axes are within a few arc-seconds and nearly still (the simulated axes
                    # approach the setpoint exponentially; firmware finishes on its step count).
                    st = self.mount.status()
                    if (abs(st.alt.position - self.goto_dest[0]) < 0.005 and abs(st.az.position - self.goto_dest[1]) < 0.005
                            and abs(st.alt.velocity) < 0.05 and abs(st.az.velocity) < 0.05):
                        self.goto_dest = None
            time.sleep(0.005)

    # ------------------------------------------------------------ commands
    def position(self) -> tuple[float, float]:
        st = self.mount.status()
        return st.alt.position, st.az.position

    def _apply_rates(self) -> None:
        a, z = self.dir["alt"] * self.rate["alt"], self.dir["az"] * self.rate["az"]
        if a == 0 and z == 0:
            self.mount.stop()
        else:
            self.mount.set_rates(a, z)

    def handle(self, cmd: str) -> str:
        """One command (``:...#`` or the ACK byte) -> exact reply bytes as text ('' for no reply)."""
        with self.lock:
            self.commands.append(cmd)
            return self._handle(cmd)

    def _handle(self, cmd: str) -> str:
        if cmd == "\x06":
            return self.mode
        body = cmd[1:-1]
        lim = self.limits
        if body in ("GVP", "GVN"):
            return "On-Step#" if body == "GVP" else "fake-0.1#"
        if body in ("GA", "GZ"):
            alt, az = self.position()
            text = format_alt(max(-90.0, min(90.0, alt))) if body == "GA" else format_az(az)
            return (text if self.high_precision else _low_precision(text)) + "#"
        if body == "GU":
            alt, az = self.position()
            st = self.mount.status()
            still = not st.alt.moving and not st.az.moving
            home = still and abs(alt) < 1e-3 and abs(math.remainder(az, 360.0)) < 1e-3
            return "n" + ("" if self.goto_dest else "N") + "p" + ("H" if home else "") + "#"
        if body == "U":
            self.high_precision = not self.high_precision
            return ""
        if body in ("Te", "Td"):
            return "1" if body == "Td" else "0"  # the fake has no firmware tracking
        if body.startswith(("Sa", "Sz")):
            try:
                v = parse_angle(body[2:])
            except LX200Error:
                return "0"
            if body[1] == "a":
                if not -90 <= v <= 90:
                    return "0"
                self.target_alt = v
            else:
                if not 0 <= v < 360:
                    return "0"
                self.target_az = v
            return "1"
        if body == "MA":
            if self.target_alt is None or self.target_az is None:
                return "9"
            if self.goto_dest is not None:
                return "5"
            if self.target_alt < lim.min_altitude:
                return "1"
            if self.target_alt > lim.max_altitude:
                return "2"
            _, cur = self.position()
            dest = cur + math.remainder(self.target_az - cur, 360.0)  # shortest way round
            if not lim.az_min <= dest <= lim.az_max:
                dest += -360.0 if dest > lim.az_max else 360.0
                if not lim.az_min <= dest <= lim.az_max:
                    return "6"
            self.dir = {"alt": 0, "az": 0}
            self.goto_dest = (self.target_alt, dest)
            self.mount.move_to(self.target_alt, dest)
            return "0"
        if body == "hC":
            _, cur = self.position()
            self.dir = {"alt": 0, "az": 0}
            self.goto_dest = (0.0, cur - math.remainder(cur, 360.0))
            self.mount.move_to(*self.goto_dest)
            return ""
        if body.startswith(("RA", "RE")):
            try:
                r = abs(float(body[2:]))
            except ValueError:
                return ""
            self.rate["az" if body[1] == "A" else "alt"] = min(r, lim.max_rate)
            if self.goto_dest is None:
                self._apply_rates()
            return ""
        if body in ("Q", "Qn", "Qs", "Qe", "Qw"):
            if body == "Q":
                self.goto_dest = None
                self.dir = {"alt": 0, "az": 0}
            else:
                self.dir["alt" if body[1] in (ALT_PLUS, ALT_MINUS) else "az"] = 0
            self._apply_rates()
            return ""
        if len(body) == 2 and body[0] == "M" and body[1] in (ALT_PLUS, ALT_MINUS, AZ_PLUS, AZ_MINUS):
            if self.goto_dest is not None:
                return ""  # ignored while a GoTo runs
            d = body[1]
            if d in (ALT_PLUS, ALT_MINUS):
                self.dir["alt"] = 1 if d == ALT_PLUS else -1
            else:
                s = 1 if d == AZ_PLUS else -1
                self.dir["az"] = -s if self.reverse_az else s
            self._apply_rates()
            return ""
        return "0"  # unknown command (OnStep answers '0')


def serve(fake: FakeOnStep, host: str = "127.0.0.1", port: int = 9999) -> socketserver.ThreadingTCPServer:
    class Handler(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            sock: socket.socket = self.request
            fake._clients.append(sock)
            buf = b""
            try:
                while True:
                    chunk = sock.recv(256)
                    if not chunk:
                        return
                    buf += chunk
                    while buf:
                        if buf[:1] == b"\x06":
                            cmd, buf = "\x06", buf[1:]
                        elif buf[:1] != b":":
                            buf = buf[1:]  # noise between commands
                            continue
                        elif b"#" in buf:
                            raw, _, buf = buf.partition(b"#")
                            cmd = raw.decode("ascii", "replace") + "#"
                        else:
                            break
                        reply = fake.handle(cmd)
                        if reply:
                            sock.sendall(reply.encode("ascii"))
            except OSError:
                return
            finally:
                with contextlib.suppress(ValueError):
                    fake._clients.remove(sock)

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    server = socketserver.ThreadingTCPServer((host, port), Handler)
    server.daemon_threads = True
    fake.start()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main() -> None:
    p = argparse.ArgumentParser(description="Simulated OnStep controller (LX200 over TCP, alt-az mode)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=9999)
    args = p.parse_args()
    fake = FakeOnStep()
    serve(fake, args.host, args.port)
    print(f"fake OnStep listening on {args.host}:{args.port} (Ctrl+C to stop)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
