"""Optional password protection for LAN use.

When a password is configured, every REST endpoint except a small allow-list and the WebSocket require either a
session cookie (obtained from ``POST /api/login``) or an ``Authorization: Bearer <password>`` header (scripts).
Sessions live in memory, so a server restart logs everybody out.

Limits (documented in docs/safety.md): plain HTTP, so the password crosses the LAN unencrypted. This keeps
casual users on the same network away from the controls; it is not protection against a hostile network.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections import OrderedDict

COOKIE = "osm_session"
MIN_PASSWORD_LEN = 6
MAX_SESSIONS = 256
FAIL_WINDOW_S = 60.0
FAIL_LIMIT = 5  # failed logins per client address per window

# Reachable without logging in. E-stop stays open on purpose: stopping is always allowed (safety rule).
OPEN_PATHS = frozenset({"/api/health", "/api/auth", "/api/login", "/api/logout", "/api/estop"})


class RateLimited(Exception):
    def __init__(self, retry_after: float) -> None:
        super().__init__("too many failed logins")
        self.retry_after = retry_after


class Auth:
    def __init__(self, password: str | None) -> None:
        if password is not None and len(password) < MIN_PASSWORD_LEN:
            raise ValueError(f"password must have at least {MIN_PASSWORD_LEN} characters")
        self._digest = hashlib.sha256(password.encode()).digest() if password else None
        self._sessions: OrderedDict[str, float] = OrderedDict()
        self._failures: dict[str, list[float]] = {}

    @property
    def required(self) -> bool:
        return self._digest is not None

    def _password_ok(self, password: str) -> bool:
        assert self._digest is not None
        return hmac.compare_digest(hashlib.sha256(password.encode()).digest(), self._digest)

    def login(self, client: str, password: str, now: float) -> str | None:
        """Return a new session token, ``None`` for a wrong password; raise ``RateLimited`` when throttled."""
        if not self.required:
            return None
        recent = [t for t in self._failures.get(client, []) if now - t < FAIL_WINDOW_S]
        if len(recent) >= FAIL_LIMIT:
            self._failures[client] = recent
            raise RateLimited(FAIL_WINDOW_S - (now - recent[0]))
        if not self._password_ok(password):
            self._failures[client] = [*recent, now]
            return None
        self._failures.pop(client, None)
        token = secrets.token_urlsafe(32)
        self._sessions[token] = now
        while len(self._sessions) > MAX_SESSIONS:
            self._sessions.popitem(last=False)
        return token

    def logout(self, token: str | None) -> None:
        if token:
            self._sessions.pop(token, None)

    def authorized(self, cookie: str | None, authorization: str | None) -> bool:
        if not self.required:
            return True
        if cookie and cookie in self._sessions:
            return True
        if authorization and authorization.lower().startswith("bearer "):
            return self._password_ok(authorization[7:].strip())
        return False
