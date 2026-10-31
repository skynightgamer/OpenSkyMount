"""OSM/1 protocol, fake firmware and ProtocolMountDriver integration tests (real time, TCP)."""

from __future__ import annotations

import socket
import time

import pytest

from openskymount.astro.engine import AstronomyEngine
from openskymount.config import AppConfig, MountLimits, SimulationConfig
from openskymount.mount.controller import CommandError, Mode, MountController
from openskymount.mount.fake_firmware import FakeFirmware, serve
from openskymount.mount.protocol import ProtocolError, Request, Status, parse_request
from openskymount.mount.protocol_driver import ProtocolMountDriver

FAST = MountLimits(max_rate=30.0, max_accel=60.0, tracking_max_rate=30.0)


def test_request_roundtrip_and_validation() -> None:
    r = parse_request(Request(7, "MOVE", [10.5, -20.25, 0.001, 0.002]).encode().decode())
    assert r.id == 7 and r.cmd == "MOVE" and r.args == [10.5, -20.25, 0.001, 0.002]
    for bad in ("", "MOVE 1 2 3 4", "x HOME", "1 FLY", "1 MOVE 1 2", "1 SYNC a b", "1 SYNC nan 0"):
        with pytest.raises(ProtocolError):
            parse_request(bad)
    s = Status(1.0, 2.0, 0.1, 0.2, None, 2.01, "HM", ["limit_alt"])
    back = Status.decode(s.encode(5).split()[2:])
    assert back == s


@pytest.fixture
def firmware():
    fw = FakeFirmware(FAST, SimulationConfig(command_latency_s=0.0, encoder_noise=0.0))
    server = serve(fw, port=0)
    port = server.server_address[1]
    yield fw, port
    server.shutdown()
    fw.shutdown()


def raw(port: int, line: str) -> str:
    with socket.create_connection(("127.0.0.1", port)) as s:
        s.sendall((line + "\n").encode())
        return s.makefile().readline().strip()


def test_firmware_replies(firmware) -> None:
    _, port = firmware
    assert raw(port, "1 HELLO").startswith("1 OK OSM/1 ")
    assert raw(port, "2 PING") == "2 PONG"
    assert raw(port, "3 MOVE 95 0 0 0").startswith("3 ERR LIMIT")
    assert raw(port, "4 DANCE").startswith("4 ERR UNKNOWN")
    assert raw(port, "5 STATUS").startswith("5 ST ")


def run_until(ctl: MountController, cond, timeout: float) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        now = time.time()
        ctl.update(now, now)
        snap = ctl.snapshot(with_radec=False)
        if cond(snap):
            return snap
        time.sleep(0.02)
    raise AssertionError(f"condition not reached; last mode {ctl.mode}, events {list(ctl.events)[-3:]}")


def make_controller(port: int) -> MountController:
    cfg = AppConfig(limits=FAST)
    drv = ProtocolMountDriver(f"tcp://127.0.0.1:{port}", cfg.effective_limits())
    now = time.time()
    return MountController(AstronomyEngine(cfg.observer), drv, cfg, now)


def test_goto_and_estop_over_tcp(firmware) -> None:
    _, port = firmware
    ctl = make_controller(port)
    caps = ctl.driver.capabilities()
    assert caps.kind == "protocol" and not caps.simulated and caps.has_axis_encoders
    run_until(ctl, lambda s: s["driver"]["connected"], 3)
    ctl.goto("altaz:30,40")
    snap = run_until(ctl, lambda s: s["mode"] == Mode.IDLE and s["tracking_error"] is not None
                     and s["tracking_error"] < 0.02, 15)
    assert abs(snap["position"]["alt"] - 30) < 0.05 and abs(snap["position"]["az"] - 40) < 0.05
    assert snap["true_pointing_error"] is None  # real-hardware path has no ground truth
    ctl.manual(0.0, 20.0, time.time())
    run_until(ctl, lambda s: abs(s["position"]["az_rate"]) > 5, 3)
    ctl.emergency_stop()
    snap = run_until(ctl, lambda s: s["driver"]["estop"], 3)
    assert snap["mode"] == Mode.STOPPED
    with pytest.raises(CommandError):
        ctl.goto("altaz:20,20")
    ctl.reset()
    run_until(ctl, lambda s: not s["driver"]["estop"], 3)


def test_solar_policy_is_strict_on_hardware_path(firmware) -> None:
    _, port = firmware
    ctl = make_controller(port)
    ctl.config.control.allow_solar_targets = True  # even if enabled, refused off-simulator
    run_until(ctl, lambda s: s["driver"]["connected"], 3)
    with pytest.raises(CommandError, match="Sun"):
        ctl.goto("sun")


def test_firmware_watchdog_stops_motion(firmware) -> None:
    fw, port = firmware
    ctl = make_controller(port)
    run_until(ctl, lambda s: s["driver"]["connected"], 3)
    ctl.driver.set_rates(0.0, 10.0)
    time.sleep(0.3)
    assert abs(fw.mount.az.vel) > 1.0
    # Host goes silent (no STATUS polls / commands): firmware must stop on its own.
    time.sleep(2.8)
    assert fw.watchdog_tripped
    assert fw.mount.az.vel == 0.0


def test_link_loss_detected(firmware) -> None:
    fw, port = firmware
    ctl = make_controller(port)
    run_until(ctl, lambda s: s["driver"]["connected"], 3)
    ctl.driver._sock.close()  # simulate a cable pull / Wi-Fi drop
    snap = run_until(ctl, lambda s: s["mode"] == Mode.DISCONNECTED, 4)
    assert "connection_lost" in snap["driver"]["faults"]
    ctl.reset()  # reconnects
    snap = run_until(ctl, lambda s: s["mode"] == Mode.IDLE and s["driver"]["connected"], 4)
