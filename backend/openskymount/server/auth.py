"""Sign-in: optional shared LAN password and optional user accounts.

Three modes, chosen by the configuration and the account file:

* **open** (default for localhost use): no password, no accounts. Everyone is a guest with operator rights.
* **password**: ``OPENSKYMOUNT_PASSWORD`` / ``--password`` is set and no accounts exist. One shared password.
* **accounts**: at least one account exists (``accounts.AccountStore``). Users log in with a user name and password;
  the role (admin / operator / observer) decides what they may do. A shared password, if set, still works as a
  fallback login with operator rights and for scripts and Alpaca clients.

Every REST endpoint except a small allow-list and the WebSocket require a session cookie (``POST /api/login``), an
``Authorization: Bearer <shared password>`` header (scripts) or HTTP Basic authentication (ASCOM Alpaca clients:
an account's user name and password, or any user name with the shared password).
Sessions live in memory, so a server restart logs everybody out.

Limits (documented in docs/safety.md): plain HTTP, so passwords cross the LAN unencrypted. This keeps casual users
on the same network away from the controls; it is not protection against a hostile network.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from .accounts import AccountStore

COOKIE = "osm_session"
MIN_PASSWORD_LEN = 6  # shared password; accounts need accounts.MIN_PASSWORD_LEN
MAX_SESSIONS = 256
SESSION_IDLE_S = 30 * 86400.0
FAIL_WINDOW_S = 60.0
FAIL_LIMIT = 5  # failed logins per client address per window
BASIC_CACHE_S = 300.0  # verified HTTP Basic credentials are cached so Alpaca polling does not rerun scrypt
BASIC_CACHE_MAX = 64

# Reachable without logging in. E-stop stays open on purpose: stopping is always allowed (safety rule).
# /api/setup checks its own permission (first account only, from this computer or after a shared-password login).
OPEN_PATHS = frozenset({"/api/health", "/api/auth", "/api/login", "/api/logout", "/api/estop", "/api/setup"})


class RateLimited(Exception):
    def __init__(self, retry_after: float) -> None:
        super().__init__("too many failed logins")
        self.retry_after = retry_after


@dataclass(frozen=True)
class Identity:
    """Who is making a request. ``kind``: open (no sign-in configured), shared (shared password) or account."""

    kind: str
    role: str
    username: str | None = None
    display_name: str = "Guest"
    token: str | None = None  # cookie session, if the request used one

    @property
    def can_control(self) -> bool:
        return self.role in ("admin", "operator")

    @property
    def is_admin(self) -> bool:
        return self.kind == "account" and self.role == "admin"

    def public(self) -> dict[str, Any]:
        return {"kind": self.kind, "role": self.role, "username": self.username, "display_name": self.display_name}


OPEN_IDENTITY = Identity("open", "operator")
SHARED_NAME = "Shared password"


@dataclass
class Session:
    token: str
    username: str | None  # None: shared-password login
    created: float
    last_seen: float
    client: str
    agent: str
    persistent: bool

    @property
    def sid(self) -> str:
        """Public id of the session (never the token itself)."""
        return hashlib.sha256(self.token.encode()).hexdigest()[:12]


def is_loopback(host: str | None) -> bool:
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


class Auth:
    def __init__(self, password: str | None, accounts: AccountStore | None = None) -> None:
        if password is not None and len(password) < MIN_PASSWORD_LEN:
            raise ValueError(f"password must have at least {MIN_PASSWORD_LEN} characters")
        self._digest = hashlib.sha256(password.encode()).digest() if password else None
        self.accounts = accounts if accounts is not None else AccountStore(None)
        self._sessions: OrderedDict[str, Session] = OrderedDict()
        self._failures: dict[str, list[float]] = {}
        self._secret = secrets.token_bytes(32)
        self._basic_cache: OrderedDict[bytes, tuple[str | None, float, int]] = OrderedDict()

    # ------------------------------------------------------------ mode
    @property
    def shared_password(self) -> bool:
        return self._digest is not None

    @property
    def required(self) -> bool:
        return self._digest is not None or self.accounts.exists()

    @property
    def mode(self) -> str:
        if self.accounts.exists():
            return "accounts"
        return "password" if self._digest is not None else "open"

    def _password_ok(self, password: str) -> bool:
        if self._digest is None:
            return False
        return hmac.compare_digest(hashlib.sha256(password.encode()).digest(), self._digest)

    # ------------------------------------------------------------ throttling
    def _check_throttle(self, client: str, now: float) -> None:
        recent = [t for t in self._failures.get(client, []) if now - t < FAIL_WINDOW_S]
        if recent:
            self._failures[client] = recent
        else:
            self._failures.pop(client, None)
        if len(recent) >= FAIL_LIMIT:
            raise RateLimited(FAIL_WINDOW_S - (now - recent[0]))

    def _fail(self, client: str, now: float) -> None:
        self._failures.setdefault(client, []).append(now)
        if len(self._failures) > 4096:  # bound memory under a scan
            self._failures.pop(next(iter(self._failures)))

    def verify_password(self, client: str, username: str, password: str, now: float) -> bool:
        """Check an account password with throttling (e.g. before changing it). Raises ``RateLimited``."""
        self._check_throttle(client, now)
        if self.accounts.verify(username, password) is None:
            self._fail(client, now)
            return False
        return True

    # ------------------------------------------------------------ sessions
    def create_session(self, username: str | None, client: str, agent: str, now: float, persistent: bool = False) -> str:
        token = secrets.token_urlsafe(32)
        self._sessions[token] = Session(token, username, now, now, client[:64], agent[:160], persistent)
        while len(self._sessions) > MAX_SESSIONS:
            self._sessions.popitem(last=False)
        if username:
            self.accounts.record_login(username, now)
        return token

    def login(self, client: str, password: str, now: float, username: str | None = None, agent: str = "",
              persistent: bool = False) -> str | None:
        """Return a new session token, ``None`` for wrong credentials; raise ``RateLimited`` when throttled.

        With a ``username`` the account password is checked; without one, the shared password.
        """
        if not self.required:
            return None
        self._check_throttle(client, now)
        if not isinstance(password, str):
            return None
        if username:
            acc = self.accounts.verify(username, password)
            if acc is None:
                self._fail(client, now)
                return None
            self._failures.pop(client, None)
            return self.create_session(acc.username, client, agent, now, persistent)
        if not self._password_ok(password):
            self._fail(client, now)
            return None
        self._failures.pop(client, None)
        return self.create_session(None, client, agent, now, persistent)

    def logout(self, token: str | None) -> None:
        if token:
            self._sessions.pop(token, None)

    def logout_user(self, username: str, keep: str | None = None) -> int:
        """End every session of an account (except ``keep``). Returns how many ended."""
        doomed = [t for t, s in self._sessions.items() if s.username == username and t != keep]
        for t in doomed:
            del self._sessions[t]
        self._basic_cache.clear()
        return len(doomed)

    def sessions_of(self, username: str) -> list[Session]:
        return [s for s in self._sessions.values() if s.username == username]

    def revoke_session(self, username: str, sid: str) -> bool:
        for t, s in list(self._sessions.items()):
            if s.username == username and s.sid == sid:
                del self._sessions[t]
                return True
        return False

    # ------------------------------------------------------------ identification
    def _session_identity(self, token: str, now: float | None) -> Identity | None:
        s = self._sessions.get(token)
        if s is None:
            return None
        if now is not None:
            if now - s.last_seen > SESSION_IDLE_S:
                del self._sessions[token]
                return None
            s.last_seen = now
        if s.username is None:
            if self._digest is None:
                return None
            return Identity("shared", "operator", None, SHARED_NAME, token)
        acc = self.accounts.get(s.username)
        if acc is None or acc.password_changed_at > s.created:
            # Account deleted, or its password was changed (also by the command-line tool) after this login.
            del self._sessions[token]
            return None
        return Identity("account", acc.role, acc.username, acc.display_name, token)

    def _basic_identity(self, header: str, client: str, now: float) -> Identity | None:
        key = hmac.new(self._secret, header.encode(), hashlib.sha256).digest()
        cached = self._basic_cache.get(key)
        if cached is not None and cached[1] > now and cached[2] == self.accounts.revision:
            return self._identity_for(cached[0])
        try:
            decoded = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None
        user, sep, pw = decoded.partition(":")
        if not sep:
            return None
        try:
            self._check_throttle(client, now)
        except RateLimited:
            return None
        username: str | None
        acc = self.accounts.verify(user, pw) if user and self.accounts.get(user) else None
        if acc is not None:
            username = acc.username
        elif self._password_ok(pw):
            username = None  # any user name with the shared password (Alpaca clients)
        else:
            self._fail(client, now)
            return None
        self._basic_cache[key] = (username, now + BASIC_CACHE_S, self.accounts.revision)
        while len(self._basic_cache) > BASIC_CACHE_MAX:
            self._basic_cache.popitem(last=False)
        return self._identity_for(username)

    def _identity_for(self, username: str | None) -> Identity | None:
        if username is None:
            return Identity("shared", "operator", None, SHARED_NAME) if self._digest is not None else None
        acc = self.accounts.get(username)
        return Identity("account", acc.role, acc.username, acc.display_name) if acc else None

    def identify(self, cookie: str | None, authorization: str | None, client: str = "?",
                 now: float | None = None) -> Identity | None:
        """The identity behind a request, or ``None`` if a login is required and the credentials are missing/wrong."""
        if not self.required:
            return OPEN_IDENTITY
        if cookie:
            ident = self._session_identity(cookie, now)
            if ident is not None:
                return ident
        if authorization:
            low = authorization.lower()
            if low.startswith("bearer "):
                if self._password_ok(authorization[7:].strip()):
                    return Identity("shared", "operator", None, SHARED_NAME)
                return None
            if low.startswith("basic "):
                return self._basic_identity(authorization, client, time.time() if now is None else now)
        return None

    def authorized(self, cookie: str | None, authorization: str | None) -> bool:
        return self.identify(cookie, authorization) is not None

    def setup_allowed(self, ident: Identity | None, client_host: str | None) -> bool:
        """May this request create the first (admin) account?

        Only while no account exists, and only from the computer running the server or after logging in with the
        shared password, so that nobody else on the network can claim the admin account first.
        """
        if self.accounts.exists():
            return False
        return is_loopback(client_host) or (ident is not None and ident.kind == "shared")
