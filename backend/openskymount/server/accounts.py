"""User accounts for signing in (optional).

Accounts are off until the first one is created. Then everybody has to log in with a user name and password (the
shared ``OPENSKYMOUNT_PASSWORD``, if one is set, keeps working for scripts, Alpaca clients and as a fallback login).

Roles:

* ``admin``: everything, plus managing accounts.
* ``operator``: may take control of the mount and change settings.
* ``observer``: view only. Cannot take control and cannot change anything over REST; E-stop and Stop still work
  (safety rule: stopping is always allowed).

Passwords are stored as scrypt hashes (random 16-byte salt per account) in ``<state>/accounts.json`` with file mode
0600. The file is re-read when it changes on disk, so the command-line tool below works while the server runs::

    python -m openskymount.server.accounts --state-dir state list
    python -m openskymount.server.accounts --state-dir state add alice --role admin
    python -m openskymount.server.accounts --state-dir state passwd alice
    python -m openskymount.server.accounts --state-dir state delete alice

There must always be at least one admin while accounts exist.
"""

from __future__ import annotations

import argparse
import contextlib
import getpass
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger("openskymount.accounts")

ROLES = ("admin", "operator", "observer")
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,31}$")
MIN_PASSWORD_LEN = 8
MAX_PASSWORD_LEN = 256
MAX_DISPLAY_NAME = 40
MAX_ACCOUNTS = 100
SCRYPT_N = 2**14  # ~40 ms per hash on a laptop; r=8, p=1, 16 MiB
SCRYPT_R = 8
SCRYPT_P = 1


class AccountError(ValueError):
    """A request that breaks an account rule (bad name, short password, last admin, ...)."""


def _hash(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=128 * 1024 * 1024, dklen=32)


def normalize_username(name: str) -> str:
    return str(name).strip().lower()


def check_username(name: str) -> str:
    u = normalize_username(name)
    if not USERNAME_RE.match(u):
        raise AccountError("user names have 2-32 characters: letters, digits, '.', '_' or '-', starting with a letter or digit")
    return u


def check_password(password: Any) -> str:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LEN:
        raise AccountError(f"passwords need at least {MIN_PASSWORD_LEN} characters")
    if len(password) > MAX_PASSWORD_LEN:
        raise AccountError(f"passwords can have at most {MAX_PASSWORD_LEN} characters")
    return password


def check_display_name(name: Any, fallback: str) -> str:
    s = " ".join(str(name or "").split())  # also removes control characters such as newlines and tabs
    s = "".join(ch for ch in s if ch.isprintable())
    if len(s) > MAX_DISPLAY_NAME:
        raise AccountError(f"display names can have at most {MAX_DISPLAY_NAME} characters")
    return s or fallback


def check_role(role: Any) -> str:
    if role not in ROLES:
        raise AccountError(f"role must be one of {', '.join(ROLES)}")
    return str(role)


@dataclass
class Account:
    username: str
    display_name: str
    role: str
    salt: str  # hex
    hash: str  # hex scrypt output
    n: int
    r: int
    p: int
    created_at: float
    password_changed_at: float
    last_login: float | None = None

    def public(self) -> dict[str, Any]:
        return {
            "username": self.username,
            "display_name": self.display_name,
            "role": self.role,
            "created_at": self.created_at,
            "password_changed_at": self.password_changed_at,
            "last_login": self.last_login,
        }


# A fixed dummy record so that checking an unknown user name costs as much as checking a real one.
_DUMMY_SALT = b"openskymount-dummy-salt"


class AccountStore:
    """Accounts in memory, persisted to ``path`` (``None``: memory only, for tests)."""

    def __init__(self, path: Path | None, scrypt_n: int = SCRYPT_N) -> None:
        self.path = path
        self.scrypt_n = scrypt_n
        self._accounts: dict[str, Account] = {}
        self._mtime: float | None = None
        #: Incremented on every change (also on reloads from disk); used to drop cached credentials.
        self.revision = 0
        if path is not None and path.exists():
            self._accounts = self._read(path)  # a damaged file is an error: never fall back to "no accounts"
            self._mtime = path.stat().st_mtime

    # ------------------------------------------------------------ persistence
    @staticmethod
    def _read(path: Path) -> dict[str, Account]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            items = data["accounts"]
            accounts = {a["username"]: Account(**a) for a in items}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise AccountError(f"cannot read the account file {path}: {exc}") from exc
        for a in accounts.values():
            if a.role not in ROLES:
                raise AccountError(f"account file {path}: unknown role {a.role!r} for {a.username!r}")
        return accounts

    def _maybe_reload(self) -> None:
        """Pick up changes made by the command-line tool while the server runs."""
        if self.path is None:
            return
        try:
            mtime: float | None = self.path.stat().st_mtime
        except FileNotFoundError:
            mtime = None
        if mtime == self._mtime:
            return
        if mtime is None:
            # The file was deleted. Keep the accounts we have: deleting the file must not open the server.
            log.warning("account file %s disappeared; keeping %d account(s) in memory", self.path, len(self._accounts))
            self._mtime = None
            if self._accounts:
                self._save()
            return
        try:
            self._accounts = self._read(self.path)
        except AccountError:
            log.exception("ignoring a damaged account file; keeping the previous accounts")
        self._mtime = mtime
        self.revision += 1

    def _save(self) -> None:
        self.revision += 1
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        data = {"version": 1, "accounts": [asdict(a) for a in self._accounts.values()]}
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
        with contextlib.suppress(OSError):
            os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)
        self._mtime = self.path.stat().st_mtime

    # ------------------------------------------------------------ queries
    def exists(self) -> bool:
        self._maybe_reload()
        return bool(self._accounts)

    def list(self) -> list[Account]:
        self._maybe_reload()
        return sorted(self._accounts.values(), key=lambda a: (ROLES.index(a.role), a.username))

    def get(self, username: str | None) -> Account | None:
        if not username:
            return None
        self._maybe_reload()
        return self._accounts.get(normalize_username(username))

    def admin_count(self) -> int:
        return sum(1 for a in self._accounts.values() if a.role == "admin")

    def verify(self, username: str, password: str) -> Account | None:
        """The account if the password matches, else ``None`` (same cost for unknown user names)."""
        acc = self.get(username)
        if not isinstance(password, str) or len(password) > MAX_PASSWORD_LEN:
            return None
        if acc is None:
            _hash(password, _DUMMY_SALT, self.scrypt_n, SCRYPT_R, SCRYPT_P)
            return None
        digest = _hash(password, bytes.fromhex(acc.salt), acc.n, acc.r, acc.p)
        return acc if hmac.compare_digest(digest.hex(), acc.hash) else None

    # ------------------------------------------------------------ changes
    def _new_hash(self, password: str) -> tuple[str, str, int, int, int]:
        salt = secrets.token_bytes(16)
        return salt.hex(), _hash(password, salt, self.scrypt_n, SCRYPT_R, SCRYPT_P).hex(), self.scrypt_n, SCRYPT_R, SCRYPT_P

    def create(self, username: str, password: str, role: str, display_name: str | None = None,
               now: float | None = None) -> Account:
        self._maybe_reload()
        u = check_username(username)
        if u in self._accounts:
            raise AccountError(f"the user name {u!r} is taken")
        if len(self._accounts) >= MAX_ACCOUNTS:
            raise AccountError(f"at most {MAX_ACCOUNTS} accounts")
        role = check_role(role)
        if not self._accounts and role != "admin":
            raise AccountError("the first account must be an admin")
        pw = check_password(password)
        now = time.time() if now is None else now
        salt, digest, n, r, p = self._new_hash(pw)
        acc = Account(u, check_display_name(display_name, u), role, salt, digest, n, r, p, now, now)
        self._accounts[u] = acc
        self._save()
        return acc

    def _require(self, username: str) -> Account:
        self._maybe_reload()
        acc = self._accounts.get(normalize_username(username))
        if acc is None:
            raise AccountError(f"no account {username!r}")
        return acc

    def update(self, username: str, *, display_name: str | None = None, role: str | None = None) -> Account:
        acc = self._require(username)
        if role is not None:
            role = check_role(role)
            if acc.role == "admin" and role != "admin" and self.admin_count() <= 1:
                raise AccountError("at least one admin account is required")
        if display_name is not None:
            acc.display_name = check_display_name(display_name, acc.username)
        if role is not None:
            acc.role = role
        self._save()
        return acc

    def set_password(self, username: str, password: str, now: float | None = None) -> Account:
        acc = self._require(username)
        pw = check_password(password)
        acc.salt, acc.hash, acc.n, acc.r, acc.p = self._new_hash(pw)
        acc.password_changed_at = time.time() if now is None else now
        self._save()
        return acc

    def delete(self, username: str) -> None:
        acc = self._require(username)
        if acc.role == "admin" and self.admin_count() <= 1:
            raise AccountError("at least one admin account is required")
        del self._accounts[acc.username]
        self._save()

    def record_login(self, username: str, now: float) -> None:
        acc = self._accounts.get(normalize_username(username))
        if acc is not None:
            acc.last_login = now
            with contextlib.suppress(OSError):
                self._save()


# ---------------------------------------------------------------- command line
def _ask_password(prompt: str = "New password: ") -> str:
    pw = getpass.getpass(prompt)
    if getpass.getpass("Repeat: ") != pw:
        raise AccountError("the passwords do not match")
    return check_password(pw)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m openskymount.server.accounts",
                                     description="Manage OpenSkyMount sign-in accounts (works while the server runs).")
    parser.add_argument("--state-dir", default=os.environ.get("OPENSKYMOUNT_STATE", "state"))
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list accounts")
    p_add = sub.add_parser("add", help="create an account (asks for the password)")
    p_add.add_argument("username")
    p_add.add_argument("--role", choices=ROLES, default="operator")
    p_add.add_argument("--display-name", default=None)
    p_pw = sub.add_parser("passwd", help="set a new password (e.g. a forgotten admin password)")
    p_pw.add_argument("username")
    p_role = sub.add_parser("role", help="change the role of an account")
    p_role.add_argument("username")
    p_role.add_argument("role", choices=ROLES)
    p_del = sub.add_parser("delete", help="delete an account")
    p_del.add_argument("username")
    args = parser.parse_args(argv)
    store = AccountStore(Path(args.state_dir) / "accounts.json")
    try:
        if args.cmd == "list":
            accounts = store.list()
            if not accounts:
                print("No accounts: sign-in is off (unless a shared password is set).")
            for a in accounts:
                print(f"{a.username:<20} {a.role:<9} {a.display_name}")
        elif args.cmd == "add":
            role = "admin" if not store.exists() else args.role
            if role != args.role:
                print("The first account is always an admin.")
            store.create(args.username, _ask_password(), role, args.display_name)
            print(f"Account {normalize_username(args.username)!r} created ({role}).")
        elif args.cmd == "passwd":
            store._require(args.username)
            store.set_password(args.username, _ask_password())
            print("Password changed. Existing logins of this account end when the server notices the change.")
        elif args.cmd == "role":
            store.update(args.username, role=args.role)
            print(f"{normalize_username(args.username)!r} is now {args.role}.")
        elif args.cmd == "delete":
            store.delete(args.username)
            print(f"Account {normalize_username(args.username)!r} deleted.")
    except AccountError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
