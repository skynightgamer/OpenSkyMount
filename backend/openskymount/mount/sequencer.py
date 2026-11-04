"""Observing session: a queue of targets the mount works through automatically (GoTo, track, next).

Deterministic like the controller: advanced with ``update(sim_time)`` after each controller update, so tests can
drive it with explicit simulation time and accelerated sessions behave the same as real-time ones.

Each item is a target id with an optional start time (wait until then before slewing) and a tracking duration
(``None``: track until the next item's start time is reached, or move on at once if the next item has no start
time). Rules:

* All motion goes through :class:`MountController.goto`, so every safety check (Sun, altitude, limits) applies.
  An item whose GoTo is refused is marked ``skipped`` with the reason, and the session continues.
* Any motion command from someone else (GoTo, Stop, E-stop, manual slew, home, reset; from the UI or Alpaca)
  pauses the session. It never resumes on its own: a person has to press Start again. A fault or E-stop latched
  by the mount pauses it too.
* If the controller ends tracking by itself (target set below the altitude limit, entered the solar exclusion
  zone, cable-wrap limit without unwind), the item ends early with that reason and the session moves on.
"""

from __future__ import annotations

import itertools
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..astro.engine import TargetError
from .controller import CommandError, Mode, MountController

MAX_ITEMS = 200
MAX_DURATION_S = 24 * 3600.0


@dataclass
class QueueItem:
    id: str
    target: str
    name: str
    start_at: float | None = None  # simulation time (Unix s); None: as soon as the previous item is done
    duration_s: float | None = 600.0  # tracking time after arrival; None: until the next item is due
    status: str = "pending"  # pending | waiting | slewing | tracking | done | skipped | interrupted
    message: str = ""
    started_at: float | None = None  # when the GoTo was issued
    tracking_since: float | None = None
    finished_at: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)


ACTIVE = ("waiting", "slewing", "tracking")


class Sequencer:
    def __init__(self, controller: MountController, path: Path | None = None) -> None:
        self.ctl = controller
        self.path = path
        self.items: list[QueueItem] = []
        self.running = False
        self.current: QueueItem | None = None
        self._serial = 0
        self._target_obj: object | None = None
        self._ids = itertools.count(1)
        self.on_finished: list[Any] = []  # callbacks when the session completes or pauses
        self._load()

    # ------------------------------------------------------------- persistence
    def _load(self) -> None:
        if not (self.path and self.path.exists()):
            return
        try:
            data = json.loads(self.path.read_text())
            self.items = [QueueItem(**it) for it in data.get("items", [])][:MAX_ITEMS]
        except (ValueError, TypeError):
            self.items = []
        for it in self.items:
            if it.status in ACTIVE:  # the server stopped mid-session
                it.status, it.message = "interrupted", "server restarted"
        nums = [int(it.id[1:]) for it in self.items if it.id[1:].isdigit()]
        self._ids = itertools.count(max(nums, default=0) + 1)

    def _save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"items": [asdict(it) for it in self.items]}, indent=1))

    # ---------------------------------------------------------------- editing
    def add(self, target_id: str, start_at: float | None = None, duration_s: float | None = 600.0,
            extra: dict[str, Any] | None = None) -> QueueItem:
        if len(self.items) >= MAX_ITEMS:
            raise CommandError(f"the queue holds at most {MAX_ITEMS} items")
        try:
            target = self.ctl.engine.resolve(target_id)
        except TargetError as exc:
            raise CommandError(str(exc)) from exc
        if target.is_sun:
            raise CommandError("the Sun cannot be added to an observing session (solar safety)")
        if start_at is not None and not math.isfinite(start_at):
            raise CommandError("start time must be a finite number")
        if duration_s is not None and not (0.0 <= duration_s <= MAX_DURATION_S):
            raise CommandError(f"duration must be between 0 and {MAX_DURATION_S:.0f} s")
        item = QueueItem(f"q{next(self._ids)}", target.id, target.name, start_at, duration_s, extra=extra or {})
        self.items.append(item)
        self._save()
        return item

    def _find(self, item_id: str) -> QueueItem:
        for it in self.items:
            if it.id == item_id:
                return it
        raise CommandError(f"no queue item {item_id!r}")

    def remove(self, item_id: str) -> None:
        it = self._find(item_id)
        if it is self.current:
            raise CommandError("pause the session or skip the item before removing it")
        self.items.remove(it)
        self._save()

    def move(self, item_id: str, delta: int) -> None:
        it = self._find(item_id)
        i = self.items.index(it)
        j = max(0, min(len(self.items) - 1, i + delta))
        self.items.insert(j, self.items.pop(i))
        self._save()

    def clear(self, finished_only: bool = False) -> None:
        if not finished_only and self.running:
            raise CommandError("pause the session before clearing the queue")
        keep = ("pending", *ACTIVE, "interrupted") if finished_only else ()
        self.items = [it for it in self.items if it.status in keep or it is self.current]
        self._save()

    def reset(self) -> None:
        """Mark every item pending again (to run a session a second time)."""
        if self.running:
            raise CommandError("pause the session first")
        for it in self.items:
            it.status, it.message, it.started_at, it.tracking_since, it.finished_at = "pending", "", None, None, None
        self.current = None
        self._save()

    # ---------------------------------------------------------------- running
    def start(self) -> str:
        if self.running:
            return "Session already running"
        if self.current is None and not any(it.status in ("pending", "interrupted") for it in self.items):
            raise CommandError("nothing left to observe: add targets or reset the queue")
        for it in self.items:
            if it.status == "interrupted":
                it.status, it.message = "pending", ""
        self.running = True
        self._serial = self.ctl.command_serial
        self.ctl.log("Observing session started")
        self._save()
        return "Observing session started"

    def pause(self, reason: str = "paused") -> None:
        if not self.running:
            return
        self.running = False
        if self.current is not None:
            self.current.status, self.current.message = "interrupted", reason
            self.current.tracking_since = None
            self.current = None
        self.ctl.log(f"Observing session paused ({reason})", "warning" if reason != "paused" else "info")
        self._save()
        for cb in self.on_finished:
            cb("paused")

    def skip(self) -> None:
        if not self.running or self.current is None:
            raise CommandError("no active queue item to skip")
        self._finish(self.current, "skipped", "skipped by user", self.ctl.sim_time)

    def _finish(self, it: QueueItem, status: str, message: str, t: float) -> None:
        it.status, it.message, it.finished_at = status, message, t
        if self.current is it:
            self.current = None
        self.ctl.log(f"Session: {it.name} {status}" + (f" ({message})" if message else ""))
        self._save()

    def _next_pending(self) -> QueueItem | None:
        return next((it for it in self.items if it.status == "pending"), None)

    def update(self, t: float) -> None:
        if not self.running:
            return
        ctl = self.ctl
        if ctl.command_serial != self._serial:
            self.pause("another command took over the mount")
            return
        if ctl.mode in (Mode.STOPPED, Mode.FAULT, Mode.DISCONNECTED):
            self.pause(f"mount {ctl.mode.replace('_', ' ')}")
            return
        it = self.current
        if it is None:
            it = self._next_pending()
            if it is None:
                self.running = False
                ctl.log("Observing session complete")
                for cb in self.on_finished:
                    cb("complete")
                return
            self.current = it
            it.status = "waiting"
        if it.status == "waiting":
            if it.start_at is not None and t < it.start_at:
                return
            try:
                ctl.goto(it.target, track=True)
            except CommandError as exc:
                self._finish(it, "skipped", str(exc), t)
                self._serial = ctl.command_serial
                return
            self._serial = ctl.command_serial
            self._target_obj = ctl.target
            it.status, it.message, it.started_at = "slewing", "", t
            self._save()
            return
        if ctl.target is not self._target_obj:  # pragma: no cover - a GoTo always bumps the serial
            self.pause("target changed")
            return
        if ctl.mode == Mode.TRACKING and it.status == "slewing":
            it.status, it.tracking_since = "tracking", t
            self._save()
        elif ctl.mode == Mode.IDLE:
            if it.status == "slewing" and ctl.target is not None and ctl.target.kind == "altaz":
                self._finish(it, "done", "arrived", t)
                return
            # The controller ended the motion on its own: take its last warning as the reason.
            since = it.started_at if it.started_at is not None else t
            why = next((e["msg"] for e in reversed(ctl.events) if e["level"] == "warning" and e["t"] >= since - 1.0),
                       "tracking ended")
            self._finish(it, "done" if it.status == "tracking" else "skipped", why, t)
            return
        if it.status == "tracking" and it.tracking_since is not None:
            if it.tracking_since > t:  # simulation time jumped backwards
                it.tracking_since = t
            if self._item_complete(it, t):
                self._finish(it, "done", "", t)

    def _item_complete(self, it: QueueItem, t: float) -> bool:
        assert it.tracking_since is not None
        if it.duration_s is not None:
            return t - it.tracking_since >= it.duration_s
        nxt = self._next_pending()
        return nxt is None or nxt.start_at is None or t >= nxt.start_at

    # ----------------------------------------------------------------- status
    def describe(self) -> dict[str, Any]:
        cur = self.current
        remaining = None
        if cur is not None and cur.status == "tracking" and cur.tracking_since is not None and cur.duration_s is not None:
            remaining = max(0.0, cur.duration_s - (self.ctl.sim_time - cur.tracking_since))
        return {
            "running": self.running,
            "current": cur.id if cur else None,
            "remaining_s": remaining,
            "items": [asdict(it) for it in self.items],
        }
