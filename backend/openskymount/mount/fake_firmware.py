"""Simulated microcontroller speaking OSM/1 over TCP.

Stands in for the future ESP32 firmware: it runs the same axis physics as the
in-process simulator, in *real time*, behind the line protocol, including the
firmware duties (watchdog, limits, E-stop latch). Use it to exercise the
``ProtocolMountDriver`` and the full network path without hardware:

    openskymount-fake-firmware --port 7624
    python -m openskymount.server --driver tcp://127.0.0.1:7624
"""

from __future__ import annotations

import argparse
import socketserver
import threading
import time

from ..config import MountLimits, SimulationConfig
from .protocol import VERSION, WATCHDOG_S, ProtocolError, Status, parse_request
from .sim_driver import SimulatedMountDriver


class FakeFirmware:
    def __init__(self, limits: MountLimits | None = None, sim: SimulationConfig | None = None,
                 clock=time.monotonic) -> None:
        self.limits = limits or MountLimits()
        self.sim = sim or SimulationConfig()
        self.clock = clock
        self.lock = threading.Lock()
        self.mount = SimulatedMountDriver(self.limits, self.sim, start_time=clock())
        self.mount.connect()
        self.last_command = clock()
        self.watchdog_tripped = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            with self.lock:
                now = self.clock()
                if now - self.last_command > WATCHDOG_S and not self.watchdog_tripped:
                    # Host silent: controlled stop, keep position.
                    self.mount.stop()
                    self.watchdog_tripped = True
                self.mount.update(now)
            time.sleep(0.01)

    def handle_line(self, line: str) -> str:
        try:
            req = parse_request(line)
        except ProtocolError as exc:
            rid = line.split()[0] if line.split() and line.split()[0].isdigit() else "0"
            return f"{rid} ERR {exc.code} {exc.message}"
        with self.lock:
            self.last_command = self.clock()
            self.watchdog_tripped = False
            m = self.mount
            lim = self.limits
            c, a = req.cmd, req.args
            if c == "HELLO":
                return (
                    f"{req.id} OK {VERSION} name=OSM-fake-firmware fw=0.1 enc={int(self.sim.has_axis_encoders)} "
                    f"home=1 vmax={lim.max_rate} amax={lim.max_accel} altmin={lim.min_altitude} "
                    f"altmax={lim.max_altitude} azmin={lim.az_min} azmax={lim.az_max}"
                )
            if c == "PING":
                return f"{req.id} PONG"
            if c == "STATUS":
                st = m.status()
                flags = ("H" if st.homed else "") + ("h" if st.homing else "") + ("E" if st.estop else "") + (
                    "M" if st.alt.moving or st.az.moving else "")
                return Status(st.alt.position, st.az.position, st.alt.velocity, st.az.velocity,
                              st.alt.encoder, st.az.encoder, flags, st.faults).encode(req.id)
            if c == "MOVE":
                if not (lim.min_altitude <= a[0] <= lim.max_altitude and lim.az_min <= a[1] <= lim.az_max):
                    return f"{req.id} ERR LIMIT target outside travel limits"
                m.move_to(*a)
            elif c == "RATE":
                m.set_rates(*a)
            elif c == "STOP":
                m.stop()
            elif c == "ESTOP":
                m.emergency_stop()
            elif c == "RESET":
                m.reset()
            elif c == "HOME":
                m.home()
            elif c == "SYNC":
                m.sync_position(*a)
            return f"{req.id} OK"


def serve(fw: FakeFirmware, host: str = "127.0.0.1", port: int = 7624) -> socketserver.ThreadingTCPServer:
    class Handler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            for raw in self.rfile:
                line = raw.decode("ascii", "replace").strip()
                if not line:
                    continue
                self.wfile.write((fw.handle_line(line) + "\n").encode("ascii"))

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    server = socketserver.ThreadingTCPServer((host, port), Handler)
    server.daemon_threads = True
    fw.start()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main() -> None:
    p = argparse.ArgumentParser(description="Simulated OSM/1 mount controller (TCP)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=7624)
    args = p.parse_args()
    fw = FakeFirmware()
    serve(fw, args.host, args.port)
    print(f"fake firmware listening on {args.host}:{args.port} (Ctrl+C to stop)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
