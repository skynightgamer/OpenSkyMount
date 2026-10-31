"""FastAPI application: REST queries, WebSocket control/telemetry, static UI."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..astro.engine import PLANETS, AstronomyEngine, TargetError
from ..clock import SimClock
from ..config import PAYLOADS, AppConfig
from ..mount.controller import CommandError, MountController
from ..mount.driver import MountDriver
from ..mount.sim_driver import INJECTABLE_FAULTS, SimulatedMountDriver
from .sessions import SessionManager

log = logging.getLogger("openskymount")

TICK_S = 0.05
BROADCAST_EVERY = 2  # ticks -> 10 Hz state updates
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def _finite(obj: Any) -> Any:
    """Replace NaN/inf (not valid JSON) with None, recursively."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _finite(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_finite(v) for v in obj]
    return obj


def make_driver(spec: str, cfg: AppConfig, now: float) -> MountDriver:
    if spec in ("", "sim", "simulator"):
        return SimulatedMountDriver(cfg.effective_limits(), cfg.sim, now)
    if spec.startswith("tcp://") or spec.startswith("serial://"):
        from ..mount.protocol_driver import ProtocolMountDriver

        return ProtocolMountDriver(spec, cfg.effective_limits())
    raise ValueError(f"unknown driver {spec!r}")


class Observatory:
    """Owns the shared state: config, clock, astronomy, driver, controller, sessions."""

    def __init__(self, state_dir: Path | None = None, driver_spec: str = "sim", clock: SimClock | None = None) -> None:
        self.state_dir = state_dir
        self.config_path = state_dir / "config.json" if state_dir else None
        self.config = AppConfig.load(self.config_path) if self.config_path else AppConfig()
        self.clock = clock or SimClock()
        self.engine = AstronomyEngine(self.config.observer)
        now = self.clock.now()
        self.driver = make_driver(driver_spec, self.config, now)
        self.controller = MountController(self.engine, self.driver, self.config, now)
        self.sessions = SessionManager(self.config.control.control_lease_s)
        self._task: asyncio.Task[None] | None = None
        self._catalog_json: bytes | None = None
        self.started = time.time()

    # ------------------------------------------------------------ main loop
    def tick(self) -> None:
        self.controller.update(self.clock.now(), time.time(), self.clock.epoch)

    async def run(self) -> None:
        n = 0
        while True:
            t0 = time.perf_counter()
            try:
                self.tick()
                n += 1
                if n % BROADCAST_EVERY == 0:
                    self.broadcast(self.state_message())
                if n % 20 == 0:
                    self._expire_control()
            except Exception:  # pragma: no cover - keep the loop alive, log loudly
                log.exception("control loop error")
            await asyncio.sleep(max(0.0, TICK_S - (time.perf_counter() - t0)))

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self.run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def _expire_control(self) -> None:
        cid = self.sessions.controller_id
        if cid and not self.sessions.controller_active(time.time()):
            self.sessions.controller_id = None
            self._on_controller_change("control lease expired")

    def _on_controller_change(self, reason: str) -> None:
        if self.controller.mode == "manual":
            self.controller.stop()
        self.controller.log(f"Control authority changed ({reason})")
        self.broadcast({"type": "control", **self.sessions.describe(time.time())})

    # -------------------------------------------------------------- messages
    def clock_info(self) -> dict[str, Any]:
        c = self.clock.state()
        return {"live": c.live, "rate": c.rate, "time": c.sim_time, "epoch": c.epoch, "wall": time.time()}

    def state_message(self) -> dict[str, Any]:
        return _finite(
            {
                "type": "state",
                "mount": self.controller.snapshot(),
                "clock": self.clock_info(),
                "control": self.sessions.describe(time.time()),
            }
        )

    def broadcast(self, msg: dict[str, Any]) -> None:
        data = json.dumps(msg)
        for s in list(self.sessions.sessions.values()):
            q = s.queue
            if isinstance(q, asyncio.Queue):
                if q.full():
                    with contextlib.suppress(asyncio.QueueEmpty):
                        q.get_nowait()  # drop the oldest message for slow clients
                q.put_nowait(data)

    def public_config(self) -> dict[str, Any]:
        return {
            "config": self.config.to_dict(),
            "payloads": {k: asdict(v) for k, v in PAYLOADS.items()},
            "effective_limits": asdict(self.config.effective_limits()),
            "capabilities": self.driver.capabilities().to_dict(),
            "injectable_faults": list(INJECTABLE_FAULTS) if self.driver.capabilities().simulated else [],
            "version": __version__,
            "plate_solver": self._solver_ok(),
        }

    def _solver_ok(self) -> bool:
        if not hasattr(self, "_solver_cache"):
            from ..solve import solver_available

            self._solver_cache = solver_available()
        return self._solver_cache

    def catalog_json(self) -> bytes:
        if self._catalog_json is None:
            self._catalog_json = json.dumps(self.engine.catalog.client_payload(), separators=(",", ":")).encode()
        return self._catalog_json

    # -------------------------------------------------------------- commands
    def apply_config(self, patch: dict[str, Any]) -> None:
        merged = self.config.to_dict()
        for key, value in patch.items():
            if key == "payload":
                merged["payload"] = value
            elif key in ("observer", "limits", "sim", "control") and isinstance(value, dict):
                merged[key].update(value)
            else:
                raise CommandError(f"unknown config section {key!r}")
        try:
            new = AppConfig.from_dict(merged)
        except (ValueError, TypeError) as exc:
            raise CommandError(str(exc)) from exc
        observer_changed = asdict(new.observer) != asdict(self.config.observer)
        mech_changed = (
            new.payload != self.config.payload
            or asdict(new.limits) != asdict(self.config.limits)
        )
        if (observer_changed or mech_changed) and self.controller.mode in ("slewing", "tracking", "manual"):
            self.controller.stop()
            self.controller.log("Configuration changed: motion stopped for safety", "warning")
        self.config = new
        self.controller.apply_config(new)
        self.sessions.lease_s = new.control.control_lease_s
        if self.config_path:
            self.config.save(self.config_path)
        self.broadcast({"type": "config", **_finite(self.public_config())})

    def set_clock(self, args: dict[str, Any]) -> None:
        if args.get("live"):
            self.clock.set_live()
            return
        if not self.driver.capabilities().supports_time_warp:
            raise CommandError("simulated time is only available with the simulated mount")
        if "time" in args:
            t = float(args["time"])
            if not (-2.2e9 < t < 2.5e9):  # DE421 covers 1900-2053
                raise CommandError("time outside the ephemeris range (1900-2050)")
            self.clock.set_time(t)
        if "rate" in args:
            try:
                self.clock.set_rate(float(args["rate"]))
            except ValueError as exc:
                raise CommandError(str(exc)) from exc
        if "paused" in args:
            self.clock.set_paused(bool(args["paused"]))

    def execute(self, sid: str, cmd: str, args: dict[str, Any]) -> dict[str, Any]:
        now = time.time()
        c = self.controller
        # Safety commands are accepted from every client.
        if cmd == "estop":
            return c.emergency_stop()
        if cmd == "stop":
            return c.stop()
        if not self.sessions.is_controller(sid, now):
            raise CommandError("this client does not have control; request control first")
        # Keep the controller's notion of time current before acting on commands.
        self.tick()
        if cmd == "goto":
            return c.goto(str(args.get("target", "")), args.get("track"))
        if cmd == "track_start":
            return c.start_tracking()
        if cmd == "track_stop":
            return c.stop_tracking()
        if cmd == "manual":
            return c.manual(float(args.get("alt_rate", 0.0)), float(args.get("az_rate", 0.0)), now)
        if cmd == "reset":
            return c.reset()
        if cmd == "home":
            return c.home()
        if cmd == "sync":
            return c.sync(str(args.get("target", "")))
        if cmd == "clear_sync":
            return c.clear_sync()
        if cmd == "apply_correction":
            return c.apply_correction(float(args["d_alt"]), float(args["d_az"]))
        if cmd == "set_clock":
            self.set_clock(args)
            return {"ok": True, "message": "Clock updated"}
        if cmd == "set_config":
            self.apply_config(args)
            return {"ok": True, "message": "Configuration updated"}
        if cmd == "align_simulated":
            from ..solve import SolverUnavailable, simulated_alignment

            try:
                res = simulated_alignment(c, apply=bool(args.get("apply", True)))
            except SolverUnavailable as exc:
                raise CommandError(str(exc)) from exc
            sol = res["solution"]
            if not sol["solved"]:
                return {"ok": False, "message": f"plate solve failed: {sol.get('reason')}", "result": res}
            corr = res.get("correction", {})
            return {
                "ok": True,
                "message": f"Solved ({sol['matches']} stars): pointing error was {corr.get('error_deg', 0) * 60:.1f}′"
                + ("; correction applied" if res.get("applied") else ""),
                "result": res,
            }
        if cmd == "inject_fault":
            try:
                self.driver.inject_fault(str(args.get("fault")), bool(args.get("active", True)))
            except (ValueError, NotImplementedError) as exc:
                raise CommandError(str(exc)) from exc
            c.log(f"Simulated fault {'injected' if args.get('active', True) else 'cleared'}: {args.get('fault')}", "warning")
            return {"ok": True, "message": "Fault updated"}
        raise CommandError(f"unknown command {cmd!r}")


def create_app(observatory: Observatory | None = None, state_dir: Path | None = None, driver: str | None = None) -> FastAPI:
    obs_holder: dict[str, Observatory] = {}

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        o = observatory or Observatory(
            state_dir if state_dir is not None else Path(os.environ.get("OPENSKYMOUNT_STATE", "state")),
            driver or os.environ.get("OPENSKYMOUNT_DRIVER", "sim"),
        )
        obs_holder["o"] = o
        app.state.observatory = o
        o.start()
        try:
            yield
        finally:
            await o.stop()

    app = FastAPI(title="OpenSkyMount", version=__version__, lifespan=lifespan)

    def obsv() -> Observatory:
        return obs_holder["o"]

    def when(t: float | None) -> float:
        return obsv().clock.now() if t is None else t

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__, "uptime": time.time() - obsv().started}

    @app.get("/api/catalog")
    def catalog() -> Response:
        return Response(obsv().catalog_json(), media_type="application/json",
                        headers={"Cache-Control": "public, max-age=3600"})

    @app.get("/api/sky")
    def sky(t: float | None = None) -> JSONResponse:
        return JSONResponse(_finite(obsv().engine.frame(when(t))))

    @app.get("/api/search")
    def search(q: str = Query(..., min_length=1, max_length=64), limit: int = Query(15, ge=1, le=50)) -> list[dict[str, Any]]:
        return obsv().engine.catalog.search(q, limit=limit, planets=PLANETS)

    @app.get("/api/object")
    def obj(id: str = Query(..., min_length=1, max_length=64), t: float | None = None) -> JSONResponse:  # noqa: A002
        o = obsv()
        try:
            target = o.engine.resolve(id)
        except TargetError as exc:
            raise HTTPException(404, str(exc)) from exc
        return JSONResponse(_finite(o.engine.info(target, when(t))))

    @app.get("/api/status")
    def status() -> JSONResponse:
        return JSONResponse(obsv().state_message())

    @app.get("/api/config")
    def config() -> JSONResponse:
        return JSONResponse(_finite(obsv().public_config()))

    @app.websocket("/ws")
    async def ws(socket: WebSocket, name: str = "client") -> None:
        await socket.accept()
        o = obsv()
        session = o.sessions.open(name, time.time())
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=8)
        session.queue = queue

        async def sender() -> None:
            while True:
                await socket.send_text(await queue.get())

        send_task = asyncio.create_task(sender())
        try:
            await socket.send_text(json.dumps({"type": "welcome", "session_id": session.id, **_finite(o.public_config())}))
            await socket.send_text(json.dumps(o.state_message()))
            o.broadcast({"type": "control", **o.sessions.describe(time.time())})
            while True:
                raw = await socket.receive_text()
                now = time.time()
                o.sessions.touch(session.id, now)
                reply = handle_message(o, session.id, raw, now)
                if reply is not None:
                    await socket.send_text(json.dumps(_finite(reply)))
        except WebSocketDisconnect:
            pass
        finally:
            send_task.cancel()
            was_controller = o.sessions.close(session.id)
            if was_controller:
                o._on_controller_change("controller disconnected")
            else:
                o.broadcast({"type": "control", **o.sessions.describe(time.time())})

    if STATIC_DIR.exists():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="ui")
    else:

        @app.get("/")
        def index() -> HTMLResponse:
            return HTMLResponse(
                "<h1>OpenSkyMount backend is running</h1><p>The web UI has not been built. "
                "Run <code>npm run build</code> in <code>frontend/</code> or use the Vite dev server.</p>"
            )

    return app


def handle_message(o: Observatory, sid: str, raw: str, now: float) -> dict[str, Any] | None:
    try:
        msg = json.loads(raw)
        if not isinstance(msg, dict):
            raise ValueError
    except ValueError:
        return {"type": "error", "message": "invalid JSON message"}
    mtype = msg.get("type")
    if mtype == "ping":
        return {"type": "pong", "t": msg.get("t")}
    if mtype == "request_control":
        granted, prev = o.sessions.request_control(sid, now, bool(msg.get("force", False)))
        if granted and prev != sid:
            o._on_controller_change("control requested" if prev is None else "control taken over")
        elif granted:
            o.broadcast({"type": "control", **o.sessions.describe(now)})
        return {"type": "control_result", "granted": granted,
                "message": None if granted else "another client is in control (use force to take over)"}
    if mtype == "release_control":
        if o.sessions.release_control(sid):
            o._on_controller_change("control released")
        return {"type": "control_result", "granted": False, "message": "released"}
    if mtype == "command":
        seq = msg.get("seq")
        cmd = str(msg.get("cmd", ""))
        if not isinstance(seq, int) or not o.sessions.check_seq(sid, seq):
            # Emergency stop is honoured even if malformed or replayed.
            if cmd == "estop":
                return {"type": "ack", "seq": seq, **o.controller.emergency_stop()}
            return {"type": "ack", "seq": seq, "ok": False, "message": "stale or missing sequence number"}
        args = msg.get("args") or {}
        if not isinstance(args, dict):
            return {"type": "ack", "seq": seq, "ok": False, "message": "args must be an object"}
        try:
            result = o.execute(sid, cmd, args)
            return {"type": "ack", "seq": seq, "cmd": cmd, **result}
        except CommandError as exc:
            return {"type": "ack", "seq": seq, "cmd": cmd, "ok": False, "message": str(exc)}
        except (KeyError, TypeError, ValueError) as exc:
            return {"type": "ack", "seq": seq, "cmd": cmd, "ok": False, "message": f"invalid arguments: {exc}"}
    return {"type": "error", "message": f"unknown message type {mtype!r}"}
