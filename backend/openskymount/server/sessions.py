"""Client sessions and control authority.

Exactly one client at a time holds *control*. Others are observers. Rules:

* Any client may take control if nobody holds it or the holder's lease expired
  (no message within ``control_lease_s``) or the holder disconnected.
* ``force`` takes control from an active holder (explicit user action: the UI asks
  for confirmation). The previous holder is notified.
* On a change of controller, ongoing *manual* motion is stopped so the new
  controller never inherits a moving joystick command.
* Emergency stop is accepted from every client, controller or not.
* Each motion command carries a monotonically increasing ``seq`` per client;
  stale or replayed commands (seq <= last seen) are rejected. A reconnecting
  client gets a new session id, so commands queued by an old connection can
  never be applied.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field


@dataclass
class ClientSession:
    id: str
    name: str
    connected_at: float
    last_seen: float
    last_seq: int = -1
    queue: object | None = None  # asyncio.Queue for outgoing messages
    meta: dict = field(default_factory=dict)


class SessionManager:
    def __init__(self, lease_s: float = 15.0) -> None:
        self.lease_s = lease_s
        self.sessions: dict[str, ClientSession] = {}
        self.controller_id: str | None = None

    def open(self, name: str, now: float) -> ClientSession:
        sid = secrets.token_hex(8)
        s = ClientSession(id=sid, name=name[:40] or "client", connected_at=now, last_seen=now)
        self.sessions[sid] = s
        return s

    def close(self, sid: str) -> bool:
        """Remove a session. Returns True if it held control."""
        self.sessions.pop(sid, None)
        if self.controller_id == sid:
            self.controller_id = None
            return True
        return False

    def touch(self, sid: str, now: float) -> None:
        if sid in self.sessions:
            self.sessions[sid].last_seen = now

    def controller_active(self, now: float) -> bool:
        if self.controller_id is None:
            return False
        s = self.sessions.get(self.controller_id)
        return s is not None and now - s.last_seen <= self.lease_s

    def request_control(self, sid: str, now: float, force: bool = False) -> tuple[bool, str | None]:
        """Returns (granted, previous_controller_id_if_changed)."""
        if sid not in self.sessions:
            return False, None
        if self.controller_id == sid:
            return True, None
        if self.controller_active(now) and not force:
            return False, None
        prev = self.controller_id
        self.controller_id = sid
        return True, prev

    def release_control(self, sid: str) -> bool:
        if self.controller_id == sid:
            self.controller_id = None
            return True
        return False

    def is_controller(self, sid: str, now: float) -> bool:
        return self.controller_id == sid and self.controller_active(now)

    def check_seq(self, sid: str, seq: int | None) -> bool:
        s = self.sessions.get(sid)
        if s is None:
            return False
        if seq is None:
            return True  # read-only messages need no sequence number
        if seq <= s.last_seq:
            return False
        s.last_seq = seq
        return True

    def describe(self, now: float) -> dict:
        return {
            "clients": [
                {"id": s.id, "name": s.name, "controller": s.id == self.controller_id}
                for s in self.sessions.values()
            ],
            "controller": self.controller_id if self.controller_active(now) else None,
            "controller_name": self.sessions[self.controller_id].name
            if self.controller_id in self.sessions and self.controller_active(now)
            else None,
        }
