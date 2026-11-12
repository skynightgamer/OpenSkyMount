"""REST endpoints for logging in and out, the first-run admin account, the user's own account and account admin.

All handlers are ``async`` because they wake WebSocket handlers (asyncio queues are not thread-safe); password
hashing (scrypt, ~40 ms) runs in the thread pool so it never stalls the 20 Hz control loop.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from .accounts import MIN_PASSWORD_LEN, ROLES, AccountError, check_password
from .auth import COOKIE, SESSION_IDLE_S, Auth, Identity, RateLimited

if TYPE_CHECKING:
    from .app import Observatory


def _client(request: Request) -> str:
    return request.client.host if request.client else "?"


def _ident(request: Request) -> Identity | None:
    return getattr(request.state, "identity", None)


def _str(value: Any) -> str:
    """A string field of a JSON body ("" if missing or not a string: the account rules then reject it)."""
    return value if isinstance(value, str) else ""


def _err(message: str, status: int, **headers: str) -> JSONResponse:
    return JSONResponse({"ok": False, "message": message}, status_code=status, headers=headers or None)


async def _body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _with_cookie(resp: JSONResponse, request: Request, token: str, persistent: bool) -> JSONResponse:
    resp.set_cookie(COOKIE, token, httponly=True, samesite="strict", path="/",
                    max_age=int(SESSION_IDLE_S) if persistent else None, secure=request.url.scheme == "https")
    return resp


def _throttled(exc: RateLimited) -> JSONResponse:
    return _err("too many attempts, wait a minute", 429, **{"Retry-After": str(max(1, round(exc.retry_after)))})


def register_account_routes(app: FastAPI, auth: Auth, observatory: Callable[[], Observatory]) -> None:
    accounts = auth.accounts

    def recheck_user(username: str) -> None:
        observatory().recheck_auth(lambda i: i is not None and i.username == username)

    def log(msg: str) -> None:
        observatory().controller.log(msg)

    @app.get("/api/auth")
    async def auth_status(request: Request) -> dict[str, Any]:
        ident = _ident(request)
        return {
            "required": auth.required,
            "authenticated": ident is not None,
            "mode": auth.mode,
            "user": ident.public() if ident else None,
            "password_login": auth.shared_password,
            "setup_allowed": auth.setup_allowed(ident, _client(request)),
            "min_password_length": MIN_PASSWORD_LEN,
            "roles": list(ROLES),
        }

    @app.post("/api/login")
    async def login(request: Request) -> JSONResponse:
        if not auth.required:
            return JSONResponse({"ok": True, "message": "sign-in is not required"})
        body = await _body(request)
        pw = body.get("password")
        username = body.get("username")
        if not isinstance(pw, str) or not pw:
            return _err("password missing", 400)
        if username is not None and not isinstance(username, str):
            return _err("invalid user name", 400)
        username = (username or "").strip() or None
        if username is None and not auth.shared_password:
            return _err("enter your user name", 400)
        try:
            token = await run_in_threadpool(auth.login, _client(request), pw, time.time(), username,
                                            request.headers.get("user-agent", ""), bool(body.get("remember")))
        except RateLimited as exc:
            return _throttled(exc)
        if token is None:
            return _err("wrong user name or password" if username else "wrong password", 401)
        ident = auth.identify(token, None)
        return _with_cookie(JSONResponse({"ok": True, "user": ident.public() if ident else None}), request, token,
                            bool(body.get("remember")))

    @app.post("/api/logout")
    async def logout(request: Request) -> JSONResponse:
        body = await _body(request)
        token = request.cookies.get(COOKIE)
        ident = auth.identify(token, None) if token else None
        ended = 0
        if body.get("everywhere") and ident is not None and ident.kind == "account" and ident.username:
            ended = auth.logout_user(ident.username)
            recheck_user(ident.username)
        else:
            auth.logout(token)
            if token:
                observatory().recheck_auth(lambda i: i is not None and i.token == token)
        resp = JSONResponse({"ok": True, "ended": max(ended, 1 if token else 0)})
        resp.delete_cookie(COOKIE, path="/")
        return resp

    @app.post("/api/setup")
    async def setup(request: Request) -> JSONResponse:
        """Create the first account (always an admin) and log it in. Turns sign-in on for everybody."""
        if accounts.exists():
            return _err("accounts already exist; ask an admin", 409)
        if not auth.setup_allowed(_ident(request), _client(request)):
            return _err("create the first account on the computer that runs OpenSkyMount (http://127.0.0.1:8000), "
                        "or log in with the shared password first", 403)
        body = await _body(request)
        try:
            acc = await run_in_threadpool(accounts.create, str(body.get("username", "")), _str(body.get("password")),
                                          "admin", body.get("display_name"), time.time())
        except AccountError as exc:
            return _err(str(exc), 400)
        token = auth.create_session(acc.username, _client(request), request.headers.get("user-agent", ""), time.time(),
                                    bool(body.get("remember", True)))
        log(f"Admin account {acc.username!r} created: sign-in is now required")
        # Every open connection was made without an account: make them log in.
        observatory().recheck_auth(lambda i: True)
        return _with_cookie(JSONResponse({"ok": True, "user": acc.public()}), request, token,
                            bool(body.get("remember", True)))

    # ---------------------------------------------------------------- own account
    def me(request: Request) -> tuple[Identity | None, JSONResponse | None]:
        ident = _ident(request)
        if ident is None or ident.kind != "account" or not ident.username:
            return None, _err("log in with an account to manage it", 403)
        return ident, None

    @app.get("/api/account")
    async def account_get(request: Request) -> JSONResponse:
        ident, err = me(request)
        if err or ident is None:
            return err  # type: ignore[return-value]
        acc = accounts.get(ident.username)
        if acc is None:
            return _err("account not found", 404)
        sessions = [
            {"id": s.sid, "created": s.created, "last_seen": s.last_seen, "client": s.client, "agent": s.agent,
             "current": s.token == ident.token}
            for s in sorted(auth.sessions_of(acc.username), key=lambda s: -s.last_seen)
        ]
        return JSONResponse({"ok": True, "user": acc.public(), "sessions": sessions})

    @app.patch("/api/account")
    async def account_patch(request: Request) -> JSONResponse:
        ident, err = me(request)
        if err or ident is None or ident.username is None:
            return err  # type: ignore[return-value]
        body = await _body(request)
        try:
            acc = accounts.update(ident.username, display_name=body.get("display_name"))
        except AccountError as exc:
            return _err(str(exc), 400)
        recheck_user(acc.username)
        return JSONResponse({"ok": True, "user": acc.public()})

    @app.post("/api/account/password")
    async def account_password(request: Request) -> JSONResponse:
        """Change the own password. Ends every other login of this account; this device gets a new session."""
        ident, err = me(request)
        if err or ident is None or ident.username is None:
            return err  # type: ignore[return-value]
        body = await _body(request)
        current, new = body.get("current_password"), body.get("new_password")
        if not isinstance(current, str) or not current:
            return _err("enter your current password", 400)
        try:
            check_password(new)
        except AccountError as exc:
            return _err(str(exc), 400)
        try:
            if not await run_in_threadpool(auth.verify_password, _client(request), ident.username, current, time.time()):
                return _err("the current password is wrong", 401)
        except RateLimited as exc:
            return _throttled(exc)
        await run_in_threadpool(accounts.set_password, ident.username, str(new), time.time())
        auth.logout_user(ident.username)
        token = auth.create_session(ident.username, _client(request), request.headers.get("user-agent", ""),
                                    time.time(), True)
        recheck_user(ident.username)
        return _with_cookie(JSONResponse({"ok": True, "message": "Password changed; other devices were logged out"}),
                            request, token, True)

    @app.delete("/api/account/sessions/{sid}")
    async def account_session_delete(sid: str, request: Request) -> JSONResponse:
        ident, err = me(request)
        if err or ident is None or ident.username is None:
            return err  # type: ignore[return-value]
        if not auth.revoke_session(ident.username, sid):
            return _err("no such login", 404)
        recheck_user(ident.username)
        return JSONResponse({"ok": True})

    # ---------------------------------------------------------------- admin
    def admin(request: Request) -> tuple[Identity | None, JSONResponse | None]:
        ident = _ident(request)
        if ident is None or not ident.is_admin:
            return None, _err("only admins can manage accounts", 403)
        return ident, None

    @app.get("/api/accounts")
    async def accounts_list(request: Request) -> JSONResponse:
        _, err = admin(request)
        if err:
            return err
        now = time.time()
        out = []
        for a in accounts.list():
            sess = auth.sessions_of(a.username)
            out.append({**a.public(), "sessions": len(sess),
                        "active": any(now - s.last_seen < 120 for s in sess)})
        return JSONResponse({"ok": True, "accounts": out, "roles": list(ROLES)})

    @app.post("/api/accounts")
    async def accounts_create(request: Request) -> JSONResponse:
        ident, err = admin(request)
        if err or ident is None:
            return err  # type: ignore[return-value]
        body = await _body(request)
        try:
            acc = await run_in_threadpool(accounts.create, str(body.get("username", "")), _str(body.get("password")),
                                          str(body.get("role", "operator")), body.get("display_name"), time.time())
        except AccountError as exc:
            return _err(str(exc), 400)
        log(f"Account {acc.username!r} ({acc.role}) created by {ident.username}")
        return JSONResponse({"ok": True, "user": acc.public()})

    @app.patch("/api/accounts/{username}")
    async def accounts_patch(username: str, request: Request) -> JSONResponse:
        ident, err = admin(request)
        if err or ident is None:
            return err  # type: ignore[return-value]
        body = await _body(request)
        try:
            if accounts.get(username) is None:
                return _err(f"no account {username!r}", 404)
            if "password" in body:
                check_password(body["password"])
            acc = accounts.update(username, display_name=body.get("display_name"), role=body.get("role"))
            if "password" in body:
                acc = await run_in_threadpool(accounts.set_password, username, str(body["password"]), time.time())
                auth.logout_user(acc.username)
        except AccountError as exc:
            return _err(str(exc), 400)
        changes = [k for k in ("role", "display_name", "password") if k in body]
        log(f"Account {acc.username!r} changed by {ident.username} ({', '.join(changes) or 'no changes'})")
        recheck_user(acc.username)
        return JSONResponse({"ok": True, "user": acc.public()})

    @app.delete("/api/accounts/{username}")
    async def accounts_delete(username: str, request: Request) -> JSONResponse:
        ident, err = admin(request)
        if err or ident is None:
            return err  # type: ignore[return-value]
        acc = accounts.get(username)
        if acc is None:
            return _err(f"no account {username!r}", 404)
        try:
            accounts.delete(acc.username)
        except AccountError as exc:
            return _err(str(exc), 400)
        auth.logout_user(acc.username)
        log(f"Account {acc.username!r} deleted by {ident.username}")
        recheck_user(acc.username)
        return JSONResponse({"ok": True})
