"""FastAPI application: REST queries, WebSocket control/telemetry, static UI."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..astro.catalog import BRIGHT_LIMIT
from ..astro.engine import AstronomyEngine, TargetError
from ..astro.orbits import ElementError, OrbitStore
from ..clock import SimClock
from ..config import PAYLOADS, AppConfig
from ..mount.controller import CommandError, Mode, MountController
from ..mount.driver import MountDriver
from ..mount.sequencer import Sequencer
from ..mount.sim_driver import INJECTABLE_FAULTS, SimulatedMountDriver
from .account_routes import register_account_routes
from .accounts import AccountStore
from .alpaca import AlpacaTelescope, connection_info, register_alpaca, start_discovery, unique_id_for
from .auth import COOKIE, OPEN_PATHS, Auth, Identity
from .recorder import RecorderError, TelemetryRecorder
from .sessions import SessionManager

log = logging.getLogger("openskymount")

TICK_S = 0.05
BROADCAST_EVERY = 2  # ticks -> 10 Hz state updates
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
AUTH_RECHECK = "\x00auth"  # queue item: re-validate the login behind this WebSocket
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


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
    from ..mount.onstep_driver import SCHEMES as LX200_SCHEMES

    if spec.startswith(LX200_SCHEMES):
        from ..mount.onstep_driver import OnStepMountDriver

        return OnStepMountDriver(spec, cfg.effective_limits())
    raise ValueError(f"unknown driver {spec!r}")


class Observatory:
    """Owns the shared state: config, clock, astronomy, driver, controller, sessions."""

    def __init__(self, state_dir: Path | None = None, driver_spec: str = "sim", clock: SimClock | None = None) -> None:
        self.state_dir = state_dir
        self.config_path = state_dir / "config.json" if state_dir else None
        self.config = AppConfig.load(self.config_path) if self.config_path else AppConfig()
        self.clock = clock or SimClock()
        self.orbits = OrbitStore(state_dir / "orbits.json" if state_dir else None)
        self.engine = AstronomyEngine(self.config.observer, orbits=self.orbits)
        now = self.clock.now()
        self.driver = make_driver(driver_spec, self.config, now)
        self.controller = MountController(self.engine, self.driver, self.config, now)
        self.sequencer = Sequencer(self.controller, state_dir / "queue.json" if state_dir else None)
        self.recorder = TelemetryRecorder(state_dir / "logs" if state_dir else None)
        self._session_recording = False  # recording started together with the observing session
        self.sequencer.on_finished.append(self._session_finished)
        self.sessions = SessionManager(self.config.control.control_lease_s)
        self._task: asyncio.Task[None] | None = None
        self.started = time.time()
        # Called about once per second with the wall time (e.g. to drop idle Alpaca clients).
        self.housekeeping: list[Callable[[float], None]] = []

    # ------------------------------------------------------------ main loop
    def tick(self) -> None:
        self.controller.update(self.clock.now(), time.time(), self.clock.epoch)
        self.sequencer.update(self.controller.sim_time)

    def _session_finished(self, why: str) -> None:
        if self._session_recording and why == "complete":
            self.recorder.stop()
            self._session_recording = False
            self.controller.log("Telemetry recording stopped (session complete)")

    async def run(self) -> None:
        n = 0
        while True:
            t0 = time.perf_counter()
            try:
                self.tick()
                n += 1
                if n % BROADCAST_EVERY == 0:
                    msg = self.state_message()
                    self.recorder.record(msg["mount"], time.time())
                    self.broadcast(msg)
                if n % 20 == 0:
                    self._expire_control()
                    for hook in self.housekeeping:
                        hook(time.time())
            except Exception:  # pragma: no cover - keep the loop alive, log loudly
                log.exception("control loop error")
            await asyncio.sleep(max(0.0, TICK_S - (time.perf_counter() - t0)))

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self.run())

    async def stop(self) -> None:
        self.recorder.stop()
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
                "session": self.sequencer.describe(),
                "recording": self.recorder.describe(),
                "orbits_revision": self.orbits.revision,
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

    def recheck_auth(self, match: Callable[[Identity | None], bool]) -> None:
        """Ask the WebSocket handlers whose identity matches to re-validate their login (account changed, logout)."""
        for s in list(self.sessions.sessions.values()):
            q = s.queue
            if isinstance(q, asyncio.Queue) and match(s.meta.get("identity")):
                if q.full():
                    with contextlib.suppress(asyncio.QueueEmpty):
                        q.get_nowait()
                q.put_nowait(AUTH_RECHECK)

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

    def catalog_json(self, tier: str = "bright") -> bytes:
        """Catalog for the browser, star positions propagated to the current year (cached per year)."""
        year = round(2000.0 + (self.clock.now() - 946728000.0) / (365.25 * 86400.0), 1)
        key = (tier, year)
        cache = self.__dict__.setdefault("_catalog_cache", {})
        if key not in cache:
            cat = self.engine.catalog
            if tier == "faint":
                payload: dict[str, Any] = {"epoch": year, "stars": cat.client_stars(BRIGHT_LIMIT, 8.0, year)}
            else:
                payload = cat.client_payload(year)
            cache.clear()
            cache[key] = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
        return cache[key]

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

    def _record_start(self, label: str) -> str:
        try:
            name = self.recorder.start(time.time(), label, {
                "site": asdict(self.config.observer), "payload": self.config.payload,
                "driver": self.driver.capabilities().to_dict(), "sim_time": self.clock.now()})
        except RecorderError as exc:
            raise CommandError(str(exc)) from exc
        self.controller.log(f"Telemetry recording started ({name})")
        return name

    def _queue_command(self, cmd: str, args: dict[str, Any]) -> dict[str, Any]:
        q = self.sequencer
        if cmd == "queue_add":
            start = args.get("start_at")
            dur = args.get("duration_s", 600.0)
            item = q.add(str(args.get("target", "")), None if start is None else float(start),
                         None if dur is None else float(dur), label=args.get("label"))
            return {"ok": True, "message": f"{item.name} added to the session", "id": item.id}
        if cmd == "queue_add_many":
            entries = args.get("items")
            if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
                raise CommandError("items must be a list of objects")
            added = q.add_many(entries, extra={"group": str(args.get("group", ""))[:40]} if args.get("group") else None)
            return {"ok": True, "message": f"{len(added)} items added to the session", "ids": [it.id for it in added]}
        if cmd == "queue_remove":
            q.remove(str(args.get("id")))
        elif cmd == "queue_move":
            q.move(str(args.get("id")), int(args.get("delta", 0)))
        elif cmd == "queue_clear":
            q.clear(bool(args.get("finished_only", False)))
        elif cmd == "queue_reset":
            q.reset()
        elif cmd == "queue_start":
            msg = q.start()
            if args.get("record") and not self.recorder.active:
                self._record_start("session")
                self._session_recording = True
            return {"ok": True, "message": msg}
        elif cmd == "queue_pause":
            q.pause()
        elif cmd == "queue_skip":
            q.skip()
        else:
            raise CommandError(f"unknown command {cmd!r}")
        return {"ok": True}

    def remove_orbit(self, target_id: str) -> None:
        c = self.controller
        if c.target is not None and c.target.id == target_id:
            if c.mode in (Mode.SLEWING, Mode.TRACKING):
                raise CommandError("the mount is following this object; stop tracking first")
            c.target, c.track = None, None
        if not self.orbits.remove(target_id):
            raise CommandError(f"no orbital elements for {target_id!r}")

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
        if cmd == "align_remove":
            return c.remove_alignment_point(int(args["index"]))
        if cmd == "clear_sync":
            return c.clear_sync()
        if cmd == "apply_correction":
            return c.apply_correction(float(args["d_alt"]), float(args["d_az"]))
        if cmd.startswith("queue_"):
            return self._queue_command(cmd, args)
        if cmd == "record_start":
            name = self._record_start(str(args.get("label", "")))
            return {"ok": True, "message": f"Recording telemetry to {name}", "name": name}
        if cmd == "record_stop":
            stopped = self.recorder.stop()
            self._session_recording = False
            if stopped:
                c.log(f"Telemetry recording stopped ({stopped})")
            return {"ok": True, "message": "Recording stopped", "name": stopped}
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
                + ("; alignment point added" if res.get("applied") else ""),
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


def create_app(
    observatory: Observatory | None = None,
    state_dir: Path | None = None,
    driver: str | None = None,
    password: str | None = None,
    alpaca: bool = True,
    alpaca_discovery: tuple[str, int] | None = None,
    listen: tuple[str, int] | None = None,
) -> FastAPI:
    """``password``: require a login for the UI and API (``None`` = open, the default for localhost use).

    ``alpaca``: serve the ASCOM Alpaca Telescope device and management API. ``alpaca_discovery``: (bind host, HTTP
    port) to answer Alpaca UDP discovery broadcasts on port 32227 (``None``: no discovery responder). ``listen``: the
    (host, port) the HTTP server is bound to, shown as Alpaca connection details (``None``: taken from the request).
    """
    obs_holder: dict[str, Observatory] = {}
    sd: Path | None = observatory.state_dir if observatory else (
        state_dir if state_dir is not None else Path(os.environ.get("OPENSKYMOUNT_STATE", "state")))
    auth = Auth(password or None, AccountStore(Path(sd) / "accounts.json" if sd else None))
    alpaca_dev: AlpacaTelescope | None = None

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        o = observatory or Observatory(
            state_dir if state_dir is not None else Path(os.environ.get("OPENSKYMOUNT_STATE", "state")),
            driver or os.environ.get("OPENSKYMOUNT_DRIVER", "sim"),
        )
        obs_holder["o"] = o
        app.state.observatory = o
        discovery = None
        if alpaca_dev is not None:
            o.housekeeping.append(alpaca_dev.purge)
            if alpaca_discovery is not None:
                discovery = await start_discovery(*alpaca_discovery)
        app.state.alpaca_discovery_active = discovery is not None
        o.start()
        try:
            yield
        finally:
            if discovery is not None:
                discovery.close()
            await o.stop()

    app = FastAPI(title="OpenSkyMount", version=__version__, lifespan=lifespan)

    app.state.auth = auth

    def obsv() -> Observatory:
        return obs_holder["o"]

    @app.middleware("http")
    async def require_login(request: Request, call_next):  # type: ignore[no-untyped-def]
        path = request.url.path
        if path.startswith("/api/"):
            ident = auth.identify(request.cookies.get(COOKIE), request.headers.get("authorization"),
                                  request.client.host if request.client else "?", time.time())
            request.state.identity = ident
            if path not in OPEN_PATHS:
                if ident is None:
                    return JSONResponse({"detail": "login required"}, status_code=401)
                if not ident.can_control and request.method not in SAFE_METHODS and not path.startswith("/api/account"):
                    return JSONResponse({"detail": "view-only account: ask an admin for operator access"},
                                        status_code=403)
        return await call_next(request)

    register_account_routes(app, auth, obsv)

    @app.post("/api/estop")
    async def estop_rest() -> dict[str, Any]:  # async: runs on the control-loop thread
        """Emergency stop without a session (login screen, scripts). Always accepted, also without the password."""
        o = obsv()
        o.controller.log("E-stop requested over HTTP", "warning")
        return o.controller.emergency_stop()

    def when(t: float | None) -> float:
        return obsv().clock.now() if t is None else t

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__, "uptime": time.time() - obsv().started}

    @app.get("/api/catalog")
    def catalog(tier: str = Query("bright", pattern="^(bright|faint)$")) -> Response:
        return Response(obsv().catalog_json(tier), media_type="application/json",
                        headers={"Cache-Control": "public, max-age=3600"})

    @app.get("/api/sky")
    def sky(t: float | None = None) -> JSONResponse:
        return JSONResponse(_finite(obsv().engine.frame(when(t))))

    @app.get("/api/search")
    def search(q: str = Query(..., min_length=1, max_length=64), limit: int = Query(15, ge=1, le=50)) -> list[dict[str, Any]]:
        return obsv().engine.search(q, limit)

    @app.get("/api/object")
    def obj(id: str = Query(..., min_length=1, max_length=64), t: float | None = None) -> JSONResponse:  # noqa: A002
        o = obsv()
        try:
            target = o.engine.resolve(id)
        except TargetError as exc:
            raise HTTPException(404, str(exc)) from exc
        return JSONResponse(_finite(o.engine.info(target, when(t))))

    @app.get("/api/slew_preview")
    def slew_preview_route(id: str = Query(..., min_length=1, max_length=64)) -> JSONResponse:  # noqa: A002
        """Read-only plan of a GoTo from the mount's present position: allowed?, duration, axis turns, cable wrap."""
        from ..mount.preview import slew_preview

        try:
            return JSONResponse(_finite(slew_preview(obsv().controller, id)))
        except CommandError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/align_stars")
    def align_stars(count: int = Query(3, ge=2, le=6)) -> JSONResponse:
        """Well-spread bright stars for a pointing alignment, in a short order to visit them."""
        from ..mount.align_stars import suggest_alignment_stars

        return JSONResponse(_finite(suggest_alignment_stars(obsv().controller, count)))

    @app.get("/api/mosaic")
    def mosaic(
        id: str = Query(..., min_length=1, max_length=64),  # noqa: A002
        t: float | None = None,
        cols: int = Query(3, ge=1, le=16),
        rows: int = Query(2, ge=1, le=16),
        overlap: float = Query(0.25, ge=0.0, le=0.6),
        fov_w: float = Query(..., ge=0.2, le=150.0),
        fov_h: float = Query(..., ge=0.2, le=150.0),
        dwell_s: float = Query(30.0, ge=0.0, le=3600.0),
    ) -> JSONResponse:
        """A grid of overlapping frames around a target, checked against the mount's limits (read-only)."""
        from ..mount.planning import mosaic_plan

        try:
            return JSONResponse(_finite(mosaic_plan(obsv().controller, id, when(t), cols, rows, overlap, fov_w, fov_h, dwell_s)))
        except CommandError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/timelapse")
    def timelapse(
        id: str = Query(..., min_length=1, max_length=64),  # noqa: A002
        t: float | None = None,
        minutes: float = Query(60.0, ge=1.0, le=720.0),
        interval_s: float = Query(10.0, ge=0.5, le=3600.0),
    ) -> JSONResponse:
        """Holding one target for a while: frames, field rotation, altitude range, cable limit (read-only)."""
        from ..mount.planning import timelapse_plan

        try:
            return JSONResponse(_finite(timelapse_plan(obsv().controller, id, when(t), minutes, interval_s)))
        except CommandError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/tonight")
    def tonight(
        t: float | None = None,
        min_alt: float = Query(30.0, ge=0.0, le=80.0),
        kind: str = Query("all", pattern="^(all|planet|dso|star)$"),
        limit: int = Query(40, ge=1, le=200),
    ) -> JSONResponse:
        from ..astro.tonight import best_targets

        o = obsv()
        unix = when(t)
        key = (round(unix / 600), min_alt, kind, limit, o.config.observer.latitude, o.config.observer.longitude)
        cache = o.__dict__.setdefault("_tonight_cache", {})
        if key not in cache:
            cache.clear()
            cache[key] = _finite(best_targets(o.engine, unix, min_alt=min_alt, kind=kind, limit=limit))
        return JSONResponse(cache[key])

    @app.get("/api/events")
    def sky_events(
        t: float | None = None,
        days: float = Query(45.0, ge=1.0, le=180.0),
        min_importance: int = Query(1, ge=1, le=3),
    ) -> JSONResponse:
        """Upcoming sky events for the observer's site (moon phases, eclipses, conjunctions, meteor showers, ...)."""
        from ..astro.events import upcoming_events

        o = obsv()
        unix = when(t)
        obs = o.config.observer
        key = (round(unix / 3600), days, obs.latitude, obs.longitude, obs.elevation_m)
        cache = o.__dict__.setdefault("_events_cache", {})
        if key not in cache:
            cache.clear()
            cache[key] = _finite(upcoming_events(o.engine, unix, days))
        data = cache[key]
        return JSONResponse({**data, "events": [e for e in data["events"] if e["importance"] >= min_importance]})

    # ---- user-supplied orbital elements (satellites, comets, asteroids)
    @app.get("/api/orbits")
    def orbits_list() -> JSONResponse:
        o = obsv()
        return JSONResponse(_finite({"objects": o.orbits.describe(o.clock.now()), "revision": o.orbits.revision}))

    @app.post("/api/orbits")
    async def orbits_add(request: Request) -> JSONResponse:
        o = obsv()
        body = await request.body()
        if len(body) > 6_000_000:
            raise HTTPException(413, "too large")
        ctype = request.headers.get("content-type", "")
        try:
            text = json.loads(body).get("text", "") if "json" in ctype else body.decode("utf-8", "replace")
        except (ValueError, AttributeError) as exc:
            raise HTTPException(400, "expected JSON {\"text\": ...} or plain text") from exc
        try:
            res = o.orbits.add_text(str(text))
        except ElementError as exc:
            raise HTTPException(400, str(exc)) from exc
        o.controller.log(f"Orbital elements loaded: {len(res['added'])} object(s)")
        o.broadcast({"type": "orbits", "revision": o.orbits.revision})
        return JSONResponse({"ok": True, **res})

    @app.delete("/api/orbits")
    async def orbits_delete(id: str | None = None) -> JSONResponse:  # noqa: A002
        o = obsv()
        try:
            if id is None:
                c = o.controller
                if c.target is not None and c.target.from_elements and c.mode in (Mode.SLEWING, Mode.TRACKING):
                    raise CommandError("the mount is following one of these objects; stop tracking first")
                if c.target is not None and c.target.from_elements:
                    c.target, c.track = None, None
                o.orbits.clear()
            else:
                o.remove_orbit(id)
        except CommandError as exc:
            raise HTTPException(409, str(exc)) from exc
        o.broadcast({"type": "orbits", "revision": o.orbits.revision})
        return JSONResponse({"ok": True})

    @app.get("/api/passes")
    def passes(
        id: str = Query(..., min_length=1, max_length=64),  # noqa: A002
        t: float | None = None,
        hours: float = Query(24.0, gt=0.0, le=72.0),
        min_alt: float = Query(10.0, ge=0.0, le=80.0),
    ) -> JSONResponse:
        o = obsv()
        try:
            target = o.engine.resolve(id)
            res = o.engine.satellite_passes(target, when(t), hours=hours, min_alt=min_alt, limit=20)
        except TargetError as exc:
            raise HTTPException(404, str(exc)) from exc
        return JSONResponse(_finite({"id": target.id, "name": target.name, "passes": res}))

    # ---- telemetry recordings
    @app.get("/api/logs")
    def logs_list() -> JSONResponse:
        o = obsv()
        return JSONResponse({"logs": o.recorder.list(), "recording": o.recorder.describe()})

    @app.get("/api/logs/{name}")
    def logs_get(name: str) -> FileResponse:
        try:
            path = obsv().recorder.path(name)
        except RecorderError as exc:
            raise HTTPException(404, str(exc)) from exc
        return FileResponse(path, media_type="application/x-ndjson", filename=name)

    @app.delete("/api/logs/{name}")
    def logs_delete(name: str) -> JSONResponse:
        try:
            obsv().recorder.delete(name)
        except RecorderError as exc:
            raise HTTPException(409, str(exc)) from exc
        return JSONResponse({"ok": True})

    @app.get("/api/status")
    def status() -> JSONResponse:
        return JSONResponse(obsv().state_message())

    @app.get("/api/config")
    def config() -> JSONResponse:
        return JSONResponse(_finite(obsv().public_config()))

    @app.get("/api/alpaca")
    async def alpaca_info(request: Request) -> dict[str, Any]:
        """Connection details for Alpaca clients (shown in Settings → Connections)."""
        return connection_info(alpaca_dev, listen, request.url.hostname, request.url.port, alpaca_discovery is not None,
                               bool(getattr(app.state, "alpaca_discovery_active", False)), auth.required, time.time())

    @app.websocket("/ws")
    async def ws(socket: WebSocket, name: str = "client") -> None:
        await socket.accept()
        creds = (socket.cookies.get(COOKIE), socket.headers.get("authorization"))
        host = socket.client.host if socket.client else "?"
        ident = auth.identify(*creds, host, time.time())
        if ident is None:
            await socket.close(code=4401, reason="login required")
            return
        o = obsv()
        device = name[:40] or "client"

        def label(i: Identity) -> str:
            return f"{i.display_name} · {device}"[:40] if i.kind == "account" else device

        session = o.sessions.open(label(ident), time.time())
        session.meta["identity"] = ident
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=8)
        session.queue = queue

        def revalidate() -> bool:
            """Re-check the login (account deleted, role or password changed, logged out). False: close."""
            i = auth.identify(*creds, host, time.time())
            if i is None:
                return False
            session.meta["identity"] = i
            session.name = label(i)
            if not i.can_control and o.sessions.release_control(session.id):
                o._on_controller_change("account is view-only")
            return True

        async def sender() -> None:
            while True:
                item = await queue.get()
                if item == AUTH_RECHECK:
                    if not revalidate():
                        with contextlib.suppress(RuntimeError):
                            await socket.close(code=4401, reason="login required")
                        return
                    await socket.send_text(json.dumps({"type": "auth_changed"}))
                    continue
                await socket.send_text(item)

        send_task = asyncio.create_task(sender())
        try:
            await socket.send_text(json.dumps({"type": "welcome", "session_id": session.id, **_finite(o.public_config())}))
            await socket.send_text(json.dumps(o.state_message()))
            o.broadcast({"type": "control", **o.sessions.describe(time.time())})
            while True:
                raw = await socket.receive_text()
                now = time.time()
                if not revalidate():
                    with contextlib.suppress(RuntimeError):
                        await socket.close(code=4401, reason="login required")
                    break
                o.sessions.touch(session.id, now)
                reply = handle_message(o, session.id, raw, now)
                if reply is not None:
                    await socket.send_text(json.dumps(_finite(reply)))
        except (WebSocketDisconnect, RuntimeError):
            pass  # RuntimeError: the sender closed the socket after a failed login check
        finally:
            send_task.cancel()
            was_controller = o.sessions.close(session.id)
            if was_controller:
                o._on_controller_change("controller disconnected")
            else:
                o.broadcast({"type": "control", **o.sessions.describe(time.time())})

    if alpaca:
        alpaca_dev = register_alpaca(app, obsv, unique_id_for(str(Path(sd).resolve()) if sd else None))
        app.state.alpaca = alpaca_dev

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
        ident = o.sessions.sessions[sid].meta.get("identity") if sid in o.sessions.sessions else None
        if isinstance(ident, Identity) and not ident.can_control:
            return {"type": "control_result", "granted": False,
                    "message": "this account is view-only (observer); ask an admin for operator access"}
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
