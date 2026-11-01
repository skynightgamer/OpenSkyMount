"""Optional LAN password: REST/WebSocket gating, login throttling, and E-stop that never needs a login."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from openskymount.server.app import Observatory, create_app
from openskymount.server.auth import COOKIE, FAIL_LIMIT, Auth

PW = "dark-skies-42"


@pytest.fixture
def obs(tmp_path) -> Observatory:
    return Observatory(state_dir=tmp_path)


@pytest.fixture
def client(obs: Observatory) -> Iterator[TestClient]:
    with TestClient(create_app(observatory=obs, password=PW)) as c:
        yield c


def test_no_password_keeps_everything_open(obs: Observatory) -> None:
    with TestClient(create_app(observatory=obs)) as c:
        assert c.get("/api/auth").json() == {"required": False, "authenticated": True}
        assert c.get("/api/config").status_code == 200
        with c.websocket_connect("/ws") as ws:
            assert json.loads(ws.receive_text())["type"] == "welcome"


def test_api_and_ws_require_login(client: TestClient) -> None:
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/auth").json() == {"required": True, "authenticated": False}
    for path in ("/api/config", "/api/status", "/api/catalog", "/api/search?q=vega"):
        assert client.get(path).status_code == 401, path
    with client.websocket_connect("/ws") as ws, pytest.raises(WebSocketDisconnect) as exc:
        ws.receive_text()
    assert exc.value.code == 4401


def test_login_cookie_grants_access_and_logout_revokes(client: TestClient) -> None:
    assert client.post("/api/login", json={"password": "wrong-one"}).status_code == 401
    r = client.post("/api/login", json={"password": PW})
    assert r.status_code == 200 and COOKIE in r.cookies
    set_cookie = r.headers["set-cookie"].lower()
    assert "httponly" in set_cookie and "samesite=strict" in set_cookie
    assert client.get("/api/auth").json()["authenticated"]
    assert client.get("/api/config").status_code == 200
    with client.websocket_connect("/ws") as ws:
        assert json.loads(ws.receive_text())["type"] == "welcome"
    client.post("/api/logout")
    assert client.get("/api/config").status_code == 401


def test_bearer_header_for_scripts(client: TestClient) -> None:
    assert client.get("/api/config", headers={"Authorization": f"Bearer {PW}"}).status_code == 200
    assert client.get("/api/config", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_failed_logins_are_throttled(client: TestClient) -> None:
    for _ in range(FAIL_LIMIT):
        assert client.post("/api/login", json={"password": "guess"}).status_code == 401
    r = client.post("/api/login", json={"password": PW})  # even the right password waits out the window
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1


def test_estop_needs_no_login(client: TestClient, obs: Observatory) -> None:
    r = client.post("/api/estop")
    assert r.status_code == 200 and r.json()["ok"]
    assert obs.controller.snapshot()["mode"] == "emergency_stop"


def test_password_rules() -> None:
    with pytest.raises(ValueError):
        Auth("short")
    a = Auth(PW)
    assert a.login("1.2.3.4", PW, 0.0) is not None
    assert not a.authorized("forged-token", None)
    assert Auth(None).authorized(None, None)
