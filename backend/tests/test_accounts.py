"""User accounts: store rules, first-run admin setup, login/logout, roles, account admin, session revocation."""

from __future__ import annotations

import base64
import json
import os
import stat
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from openskymount.server import accounts as accounts_mod
from openskymount.server.accounts import AccountError, AccountStore
from openskymount.server.app import Observatory, create_app
from openskymount.server.auth import COOKIE, FAIL_LIMIT, Auth

LOCAL = ("127.0.0.1", 50000)
REMOTE = ("192.168.1.50", 50000)
ADMIN_PW = "orion-nebula-42"
OP_PW = "vega-lyra-2026"
OBS_PW = "watch-only-9"


# ---------------------------------------------------------------- store
def test_store_hashes_and_persists_with_private_mode(tmp_path: Path) -> None:
    path = tmp_path / "accounts.json"
    store = AccountStore(path)
    acc = store.create("Alice", ADMIN_PW, "admin", "  Alice\nLiddell ")
    assert acc.username == "alice" and acc.display_name == "Alice Liddell"
    raw = path.read_text()
    assert ADMIN_PW not in raw and acc.hash in raw
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    again = AccountStore(path)
    assert again.verify("ALICE", ADMIN_PW) is not None
    assert again.verify("alice", "wrong-password") is None
    assert again.verify("nobody", ADMIN_PW) is None


def test_store_rules(tmp_path: Path) -> None:
    store = AccountStore(tmp_path / "accounts.json")
    with pytest.raises(AccountError, match="first account must be an admin"):
        store.create("bob", OP_PW, "operator")
    with pytest.raises(AccountError, match="at least 8"):
        store.create("bob", "short", "admin")
    for bad in ("a", "-dash", "has space", "x" * 33, "ümlaut"):
        with pytest.raises(AccountError):
            store.create(bad, ADMIN_PW, "admin")
    store.create("root", ADMIN_PW, "admin")
    with pytest.raises(AccountError, match="taken"):
        store.create("ROOT", ADMIN_PW, "admin")
    with pytest.raises(AccountError, match="role"):
        store.create("eve", OP_PW, "superuser")
    # The last admin can be neither demoted nor deleted.
    with pytest.raises(AccountError, match="at least one admin"):
        store.update("root", role="operator")
    with pytest.raises(AccountError, match="at least one admin"):
        store.delete("root")
    store.create("second", ADMIN_PW, "admin")
    store.update("root", role="observer")
    store.delete("root")
    assert [a.username for a in store.list()] == ["second"]


def test_store_reloads_changes_made_on_disk(tmp_path: Path) -> None:
    path = tmp_path / "accounts.json"
    server = AccountStore(path)
    server.create("root", ADMIN_PW, "admin")
    cli = AccountStore(path)
    cli.create("bob", OP_PW, "operator")
    os.utime(path, (1, 1))  # make sure the modification time differs on coarse file systems
    assert server.get("bob") is not None
    # A damaged file is refused at start-up (never silently "no accounts" = open server) ...
    path.write_text("{not json")
    with pytest.raises(AccountError):
        AccountStore(path)
    # ... and ignored by a running server, which keeps the accounts it has.
    os.utime(path, (2, 2))
    assert server.exists() and server.get("bob") is not None


def test_cli_add_and_passwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    answers = iter([ADMIN_PW, ADMIN_PW, "new-password-1", "new-password-1"])
    monkeypatch.setattr(accounts_mod.getpass, "getpass", lambda prompt="": next(answers))
    assert accounts_mod.main(["--state-dir", str(tmp_path), "add", "carol", "--role", "observer"]) == 0
    assert "first account is always an admin" in capsys.readouterr().out
    assert accounts_mod.main(["--state-dir", str(tmp_path), "passwd", "carol"]) == 0
    store = AccountStore(tmp_path / "accounts.json")
    assert store.get("carol").role == "admin"  # type: ignore[union-attr]
    assert store.verify("carol", "new-password-1") is not None
    assert accounts_mod.main(["--state-dir", str(tmp_path), "delete", "carol"]) == 1  # last admin


# ---------------------------------------------------------------- HTTP helpers
@pytest.fixture
def obs(tmp_path: Path) -> Observatory:
    return Observatory(state_dir=tmp_path)


def client(obs: Observatory, addr: tuple[str, int] = LOCAL, password: str | None = None) -> TestClient:
    return TestClient(create_app(observatory=obs, password=password), client=addr)


def cookie(token: str) -> dict[str, str]:
    return {"Cookie": f"{COOKIE}={token}"}


def take_token(c: TestClient, r) -> str:  # type: ignore[no-untyped-def]
    assert r.status_code == 200, r.text
    token = r.cookies[COOKIE]
    c.cookies.clear()  # every request below says explicitly who it is
    return token


def login(c: TestClient, user: str | None, pw: str) -> str:
    return take_token(c, c.post("/api/login", json={"username": user, "password": pw}))


@pytest.fixture
def setup_admin(obs: Observatory) -> Iterator[tuple[TestClient, str]]:
    with client(obs) as c:
        token = take_token(c, c.post("/api/setup", json={"username": "root", "password": ADMIN_PW, "display_name": "Root"}))
        yield c, token


def add(c: TestClient, admin: str, user: str, pw: str, role: str) -> None:
    r = c.post("/api/accounts", json={"username": user, "password": pw, "role": role}, headers=cookie(admin))
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------- setup
def test_first_admin_only_from_this_computer(obs: Observatory) -> None:
    with client(obs, REMOTE) as c:
        a = c.get("/api/auth").json()
        assert a["mode"] == "open" and a["setup_allowed"] is False
        r = c.post("/api/setup", json={"username": "mallory", "password": ADMIN_PW})
        assert r.status_code == 403
    with client(obs, LOCAL) as c:
        assert c.get("/api/auth").json()["setup_allowed"] is True
        token = take_token(c, c.post("/api/setup", json={"username": "root", "password": ADMIN_PW}))
        a = c.get("/api/auth", headers=cookie(token)).json()
        assert a["mode"] == "accounts" and a["user"] == {"kind": "account", "role": "admin", "username": "root", "display_name": "root"}
        assert c.post("/api/setup", json={"username": "two", "password": ADMIN_PW}).status_code == 409
        # Sign-in is now required for everybody, including this computer.
        assert c.get("/api/config").status_code == 401
        assert c.get("/api/config", headers=cookie(token)).status_code == 200


def test_setup_from_the_network_after_shared_password_login(obs: Observatory) -> None:
    with client(obs, REMOTE, password="shared-pw") as c:
        shared = login(c, None, "shared-pw")
        assert c.get("/api/auth", headers=cookie(shared)).json()["setup_allowed"] is True
        token = take_token(c, c.post("/api/setup", json={"username": "root", "password": ADMIN_PW}, headers=cookie(shared)))
        assert c.get("/api/auth", headers=cookie(token)).json()["mode"] == "accounts"
        # The shared password keeps working as an operator login, but cannot manage accounts.
        shared2 = login(c, None, "shared-pw")
        me = c.get("/api/auth", headers=cookie(shared2)).json()["user"]
        assert me["kind"] == "shared" and me["role"] == "operator"
        assert c.get("/api/accounts", headers=cookie(shared2)).status_code == 403
        assert c.get("/api/config", headers={"Authorization": "Bearer shared-pw"}).status_code == 200


def test_open_websocket_clients_must_log_in_after_setup(obs: Observatory) -> None:
    with client(obs) as c, c.websocket_connect("/ws") as ws:
        assert json.loads(ws.receive_text())["type"] == "welcome"
        take_token(c, c.post("/api/setup", json={"username": "root", "password": ADMIN_PW}))
        with pytest.raises(WebSocketDisconnect) as exc:
            while True:
                ws.receive_text()
        assert exc.value.code == 4401


# ---------------------------------------------------------------- login / roles
def test_login_logout_and_throttling(setup_admin: tuple[TestClient, str]) -> None:
    c, admin = setup_admin
    assert c.post("/api/login", json={"username": "root", "password": "nope-nope"}).status_code == 401
    assert c.post("/api/login", json={"password": ADMIN_PW}).status_code == 400  # no shared password: name needed
    token = login(c, "Root", ADMIN_PW)
    assert c.get("/api/config", headers=cookie(token)).status_code == 200
    c.post("/api/logout", headers=cookie(token))
    assert c.get("/api/config", headers=cookie(token)).status_code == 401
    assert c.get("/api/config", headers=cookie(admin)).status_code == 200  # other logins stay
    for _ in range(FAIL_LIMIT):
        c.post("/api/login", json={"username": "root", "password": "guess-1234"})
    r = c.post("/api/login", json={"username": "root", "password": ADMIN_PW})
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1


def test_remember_me_sets_a_persistent_cookie(setup_admin: tuple[TestClient, str]) -> None:
    c, _ = setup_admin
    r = c.post("/api/login", json={"username": "root", "password": ADMIN_PW, "remember": True})
    assert "max-age=" in r.headers["set-cookie"].lower()
    c.cookies.clear()
    r = c.post("/api/login", json={"username": "root", "password": ADMIN_PW})
    assert "max-age=" not in r.headers["set-cookie"].lower()
    c.cookies.clear()


def test_observer_is_view_only_but_can_stop(setup_admin: tuple[TestClient, str], obs: Observatory) -> None:
    c, admin = setup_admin
    add(c, admin, "watcher", OBS_PW, "observer")
    tok = login(c, "watcher", OBS_PW)
    h = cookie(tok)
    assert c.get("/api/status", headers=h).status_code == 200
    assert c.post("/api/orbits", json={"text": ""}, headers=h).status_code == 403
    assert c.get("/api/accounts", headers=h).status_code == 403
    with c.websocket_connect("/ws", headers=h) as ws:
        assert json.loads(ws.receive_text())["type"] == "welcome"
        ws.send_text(json.dumps({"type": "request_control"}))
        while (m := json.loads(ws.receive_text()))["type"] != "control_result":
            pass
        assert m["granted"] is False and "view-only" in m["message"]
        ws.send_text(json.dumps({"type": "command", "seq": 1, "cmd": "estop"}))
        while (m := json.loads(ws.receive_text()))["type"] != "ack":
            pass
        assert m["ok"]
    assert obs.controller.snapshot()["mode"] == "emergency_stop"
    assert c.post("/api/estop", headers=h).status_code == 200


def test_operator_controls_and_shows_display_name(setup_admin: tuple[TestClient, str], obs: Observatory) -> None:
    c, admin = setup_admin
    r = c.post("/api/accounts", json={"username": "op", "password": OP_PW, "role": "operator", "display_name": "Night Owl"},
               headers=cookie(admin))
    assert r.status_code == 200
    tok = login(c, "op", OP_PW)
    with c.websocket_connect("/ws?name=desktop-ab12", headers=cookie(tok)) as ws:
        ws.receive_text()
        ws.send_text(json.dumps({"type": "request_control"}))
        while (m := json.loads(ws.receive_text()))["type"] != "control_result":
            pass
        assert m["granted"]
        assert obs.sessions.describe(0)["clients"][0]["name"] == "Night Owl · desktop-ab12"


# ---------------------------------------------------------------- admin
def test_admin_manages_accounts(setup_admin: tuple[TestClient, str]) -> None:
    c, admin = setup_admin
    h = cookie(admin)
    add(c, admin, "bob", OP_PW, "operator")
    assert c.post("/api/accounts", json={"username": "bob", "password": OP_PW}, headers=h).status_code == 400
    names = [a["username"] for a in c.get("/api/accounts", headers=h).json()["accounts"]]
    assert names == ["root", "bob"]
    bob = login(c, "bob", OP_PW)
    assert c.get("/api/accounts", headers=cookie(bob)).status_code == 403
    # Role change applies to existing logins at once.
    assert c.patch("/api/accounts/bob", json={"role": "observer"}, headers=h).json()["user"]["role"] == "observer"
    assert c.get("/api/auth", headers=cookie(bob)).json()["user"]["role"] == "observer"
    # A password reset ends the user's logins.
    assert c.patch("/api/accounts/bob", json={"password": "reset-by-admin"}, headers=h).status_code == 200
    assert c.get("/api/config", headers=cookie(bob)).status_code == 401
    assert login(c, "bob", "reset-by-admin")
    # The only admin cannot remove themselves.
    assert c.patch("/api/accounts/root", json={"role": "operator"}, headers=h).status_code == 400
    assert c.delete("/api/accounts/root", headers=h).status_code == 400
    assert c.delete("/api/accounts/bob", headers=h).status_code == 200
    assert c.delete("/api/accounts/bob", headers=h).status_code == 404


def test_deleted_user_websocket_is_closed(setup_admin: tuple[TestClient, str]) -> None:
    c, admin = setup_admin
    add(c, admin, "bob", OP_PW, "operator")
    bob = login(c, "bob", OP_PW)
    with c.websocket_connect("/ws", headers=cookie(bob)) as ws:
        assert json.loads(ws.receive_text())["type"] == "welcome"
        assert c.delete("/api/accounts/bob", headers=cookie(admin)).status_code == 200
        with pytest.raises(WebSocketDisconnect) as exc:
            while True:
                ws.receive_text()
        assert exc.value.code == 4401


def test_demoted_controller_loses_control(setup_admin: tuple[TestClient, str], obs: Observatory) -> None:
    c, admin = setup_admin
    add(c, admin, "bob", OP_PW, "operator")
    bob = login(c, "bob", OP_PW)
    with c.websocket_connect("/ws", headers=cookie(bob)) as ws:
        ws.receive_text()
        ws.send_text(json.dumps({"type": "request_control"}))
        while (m := json.loads(ws.receive_text()))["type"] != "control_result":
            pass
        assert m["granted"]
        c.patch("/api/accounts/bob", json={"role": "observer"}, headers=cookie(admin))
        while json.loads(ws.receive_text())["type"] != "auth_changed":
            pass
        assert obs.sessions.controller_id is None


# ---------------------------------------------------------------- own account
def test_own_profile_password_and_sessions(setup_admin: tuple[TestClient, str]) -> None:
    c, admin = setup_admin
    add(c, admin, "bob", OP_PW, "operator")
    phone = login(c, "bob", OP_PW)
    laptop = login(c, "bob", OP_PW)
    me = c.get("/api/account", headers=cookie(laptop)).json()
    assert len(me["sessions"]) == 2 and sum(s["current"] for s in me["sessions"]) == 1
    r = c.patch("/api/account", json={"display_name": "Bob the Builder"}, headers=cookie(laptop))
    assert r.json()["user"]["display_name"] == "Bob the Builder"
    # Wrong current password, too short a new one.
    r = c.post("/api/account/password", json={"current_password": "x" * 9, "new_password": "brand-new-pw"}, headers=cookie(laptop))
    assert r.status_code == 401
    r = c.post("/api/account/password", json={"current_password": OP_PW, "new_password": "short"}, headers=cookie(laptop))
    assert r.status_code == 400
    r = c.post("/api/account/password", json={"current_password": OP_PW, "new_password": "brand-new-pw"}, headers=cookie(laptop))
    fresh = take_token(c, r)
    assert c.get("/api/config", headers=cookie(phone)).status_code == 401
    assert c.get("/api/config", headers=cookie(laptop)).status_code == 401
    assert c.get("/api/config", headers=cookie(fresh)).status_code == 200
    # End another login by its public id.
    other = login(c, "bob", "brand-new-pw")
    sid = next(s["id"] for s in c.get("/api/account", headers=cookie(fresh)).json()["sessions"] if not s["current"])
    assert c.delete(f"/api/account/sessions/{sid}", headers=cookie(fresh)).status_code == 200
    assert c.get("/api/config", headers=cookie(other)).status_code == 401
    # Log out everywhere.
    c.post("/api/logout", json={"everywhere": True}, headers=cookie(fresh))
    assert c.get("/api/config", headers=cookie(fresh)).status_code == 401
    # Shared/guest identities have no account to manage.
    assert c.get("/api/account").status_code == 401


def test_password_changed_with_the_cli_ends_logins(setup_admin: tuple[TestClient, str], tmp_path: Path) -> None:
    c, admin = setup_admin
    cli = AccountStore(tmp_path / "accounts.json")
    cli.set_password("root", "changed-on-disk")
    os.utime(tmp_path / "accounts.json", (5, 5))
    assert c.get("/api/config", headers=cookie(admin)).status_code == 401
    assert login(c, "root", "changed-on-disk")


def test_basic_auth_with_account_for_alpaca(setup_admin: tuple[TestClient, str]) -> None:
    c, admin = setup_admin
    add(c, admin, "nina", OP_PW, "operator")
    add(c, admin, "viewer", OBS_PW, "observer")
    good = "Basic " + base64.b64encode(f"nina:{OP_PW}".encode()).decode()
    bad = "Basic " + base64.b64encode(b"nina:wrong-password").decode()
    view = "Basic " + base64.b64encode(f"viewer:{OBS_PW}".encode()).decode()
    url = "/api/v1/telescope/0/name"
    assert c.get(url, headers={"Authorization": good}).status_code == 200
    assert c.get(url, headers={"Authorization": good}).status_code == 200  # cached
    assert c.get(url, headers={"Authorization": bad}).status_code == 401
    assert c.get(url, headers={"Authorization": view}).status_code == 200
    assert c.put("/api/v1/telescope/0/connected", data={"Connected": "true"}, headers={"Authorization": view}).status_code == 403
    c.delete("/api/accounts/nina", headers=cookie(admin))
    assert c.get(url, headers={"Authorization": good}).status_code == 401


def test_auth_identity_unit() -> None:
    a = Auth(None)
    assert a.mode == "open" and a.identify(None, None) is not None
    a.accounts.create("root", ADMIN_PW, "admin")
    assert a.mode == "accounts" and a.identify(None, None) is None
    tok = a.login("10.0.0.1", ADMIN_PW, time.time(), username="root")
    ident = a.identify(tok, None)
    assert ident is not None and ident.is_admin and ident.can_control
