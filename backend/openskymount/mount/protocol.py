"""OSM/1 line protocol between the host and the mount microcontroller.

ASCII, one command per line (``\\n``), designed to be trivial to parse on an ESP32.
Every request starts with a client-chosen integer id that is echoed in the reply.

Host -> MCU                               MCU -> host
-----------------------------------------------------------------------------
<id> HELLO                                <id> OK OSM/1 key=value ...
<id> PING                                 <id> PONG
<id> STATUS                               <id> ST alt az valt vaz ealt eaz flags faults
<id> MOVE alt az alt_rate az_rate         <id> OK        (position + rate setpoint)
<id> RATE alt_rate az_rate                <id> OK        (velocity mode, manual)
<id> STOP                                 <id> OK        (controlled deceleration)
<id> ESTOP                                <id> OK        (immediate, latched)
<id> RESET                                <id> OK        (clear latched faults)
<id> HOME                                 <id> OK        (start homing sequence)
<id> SYNC alt az                          <id> OK        (redefine position reference)
any malformed / refused request           <id> ERR CODE message

Units: degrees and degrees/second, axis (mount) coordinates, azimuth multi-turn.
``ST`` fields: encoder values are ``nan`` if no axis encoder is fitted; ``flags``
is a string of letters (H homed, h homing, E e-stop, M moving) or ``-``; ``faults``
is a comma-separated list of fault codes (see ``driver.Fault``) or ``-``.

Firmware safety duties (not optional): enforce soft and hard travel limits,
stop if no valid command arrives within the watchdog period (2 s), start up with
motors disabled until HOME or SYNC, honour ESTOP immediately. A hardware
emergency-stop switch must cut motor power independently of this protocol.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

VERSION = "OSM/1"
WATCHDOG_S = 2.0

ARG_COUNTS = {
    "HELLO": 0, "PING": 0, "STATUS": 0, "MOVE": 4, "RATE": 2, "STOP": 0,
    "ESTOP": 0, "RESET": 0, "HOME": 0, "SYNC": 2,
}


class ProtocolError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass
class Request:
    id: int
    cmd: str
    args: list[float]

    def encode(self) -> bytes:
        parts = [str(self.id), self.cmd, *(f"{a:.6f}" for a in self.args)]
        return (" ".join(parts) + "\n").encode("ascii")


def parse_request(line: str) -> Request:
    parts = line.strip().split()
    if len(parts) < 2:
        raise ProtocolError("SYNTAX", "expected '<id> <COMMAND> [args]'")
    try:
        rid = int(parts[0])
    except ValueError as exc:
        raise ProtocolError("SYNTAX", "request id must be an integer") from exc
    cmd = parts[1].upper()
    if cmd not in ARG_COUNTS:
        raise ProtocolError("UNKNOWN", f"unknown command {cmd}")
    if len(parts) - 2 != ARG_COUNTS[cmd]:
        raise ProtocolError("ARGS", f"{cmd} takes {ARG_COUNTS[cmd]} arguments")
    try:
        args = [float(x) for x in parts[2:]]
    except ValueError as exc:
        raise ProtocolError("ARGS", "arguments must be numbers") from exc
    if not all(math.isfinite(a) for a in args):
        raise ProtocolError("ARGS", "arguments must be finite")
    return Request(rid, cmd, args)


@dataclass
class Status:
    alt: float
    az: float
    v_alt: float
    v_az: float
    enc_alt: float | None
    enc_az: float | None
    flags: str
    faults: list[str]

    def encode(self, rid: int) -> str:
        def f(x: float | None) -> str:
            return "nan" if x is None else f"{x:.6f}"

        return (
            f"{rid} ST {self.alt:.6f} {self.az:.6f} {self.v_alt:.6f} {self.v_az:.6f} "
            f"{f(self.enc_alt)} {f(self.enc_az)} {self.flags or '-'} {','.join(self.faults) or '-'}"
        )

    @classmethod
    def decode(cls, fields: list[str]) -> Status:
        if len(fields) != 8:
            raise ProtocolError("SYNTAX", "ST expects 8 fields")
        nums = [float(x) for x in fields[:6]]
        return cls(
            alt=nums[0], az=nums[1], v_alt=nums[2], v_az=nums[3],
            enc_alt=None if math.isnan(nums[4]) else nums[4],
            enc_az=None if math.isnan(nums[5]) else nums[5],
            flags="" if fields[6] == "-" else fields[6],
            faults=[] if fields[7] == "-" else fields[7].split(","),
        )


def parse_hello(fields: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for f in fields:
        if "=" in f:
            k, v = f.split("=", 1)
            out[k] = v
    return out
