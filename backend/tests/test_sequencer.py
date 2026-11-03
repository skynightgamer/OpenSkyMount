"""Observing session queue: GoTo, track, next; skipping, waiting for start times, interruptions."""

from __future__ import annotations

import datetime as dt

import pytest

from openskymount.config import AppConfig
from openskymount.mount.controller import CommandError
from openskymount.mount.sequencer import Sequencer

from .conftest import T0, Rig
from .test_orbits import ISS


def run(rig: Rig, seq: Sequencer, seconds: float, step: float = 1.0) -> None:
    end = rig.t + seconds
    while rig.t < end - 1e-9:
        rig.run(step, dt=min(step, 0.25))
        seq.update(rig.t)


def by_name(seq: Sequencer, name: str):
    return next(it for it in seq.items if name in it.name)


def test_session_works_through_the_queue(rig: Rig, tmp_path) -> None:
    seq = Sequencer(rig.ctl, tmp_path / "queue.json")
    seq.add("Capella", duration_s=120)
    seq.add("Antares", duration_s=60)  # far below the horizon from Berlin in a November night
    seq.add("planet:jupiter", duration_s=None)  # until the next item is due
    seq.add("dso:M42", start_at=T0 + 900, duration_s=60)
    seq.start()
    run(rig, seq, 1300)
    cap, ant, jup, m42 = seq.items
    assert cap.status == "done" and cap.tracking_since is not None
    assert cap.finished_at - cap.tracking_since == pytest.approx(120, abs=1.5)
    assert ant.status == "skipped" and "below the altitude limit" in ant.message
    assert jup.status == "done" and jup.finished_at == pytest.approx(T0 + 900, abs=1.5)
    # M42 was not started before its start time, then slewed, tracked for 60 s and finished.
    assert m42.status == "done" and m42.started_at >= T0 + 900
    assert m42.finished_at - m42.tracking_since == pytest.approx(60, abs=1.5)
    assert not seq.running and seq.current is None
    assert any("session complete" in e["msg"] for e in rig.ctl.events)
    # Persisted: a new sequencer (server restart) reads the finished queue back.
    again = Sequencer(rig.ctl, tmp_path / "queue.json")
    assert [it.status for it in again.items] == ["done", "skipped", "done", "done"]
    with pytest.raises(CommandError, match="nothing left"):
        again.start()
    again.reset()
    assert all(it.status == "pending" for it in again.items)


def test_any_other_command_pauses_the_session(rig: Rig) -> None:
    seq = Sequencer(rig.ctl)
    seq.add("Capella", duration_s=600)
    seq.add("planet:jupiter", duration_s=600)
    seq.start()
    run(rig, seq, 60)
    assert seq.items[0].status == "tracking"
    rig.ctl.stop()  # a person presses Stop (or any client: E-stop, manual, GoTo, Alpaca)
    run(rig, seq, 30)
    assert not seq.running and seq.items[0].status == "interrupted"
    assert rig.ctl.mode == "idle"  # nothing restarted the motion
    assert seq.items[1].status == "pending"
    seq.start()  # resumes with the interrupted item
    run(rig, seq, 60)
    assert seq.items[0].status == "tracking"
    rig.ctl.emergency_stop()
    run(rig, seq, 5)
    assert not seq.running and rig.ctl.mode == "emergency_stop"


def test_mount_fault_pauses_and_sun_is_refused(rig: Rig) -> None:
    seq = Sequencer(rig.ctl)
    with pytest.raises(CommandError, match="Sun"):
        seq.add("sun")
    with pytest.raises(CommandError):
        seq.add("not-a-real-object")
    with pytest.raises(CommandError, match="duration"):
        seq.add("Capella", duration_s=-5)
    seq.add("Capella", duration_s=600)
    seq.start()
    run(rig, seq, 20)
    rig.driver.inject_fault("overcurrent")
    run(rig, seq, 5)
    assert not seq.running and seq.items[0].status == "interrupted"
    with pytest.raises(CommandError):
        seq.remove("q99")
    seq.move(seq.items[0].id, 3)
    seq.clear()
    assert seq.items == []


def test_item_ends_when_the_target_sets() -> None:
    """A satellite pass queued with a long duration ends when the ISS drops below the altitude limit."""
    rise = dt.datetime(2014, 1, 21, 3, 27, 10, tzinfo=dt.UTC).timestamp()
    cfg = AppConfig()
    cfg.control.min_target_altitude = 10.0
    rig = Rig(cfg, t0=rise - 120)
    rig.engine.orbits.add_text(ISS)
    seq = Sequencer(rig.ctl)
    seq.add("sat:25544", start_at=rise + 5, duration_s=3600)
    seq.add("Capella", duration_s=30)
    seq.start()
    run(rig, seq, 100, step=0.5)
    assert seq.items[0].status == "waiting" and rig.ctl.mode == "idle"
    run(rig, seq, 520, step=0.5)
    iss, cap = seq.items
    assert iss.status == "done" and "below the altitude limit" in iss.message
    assert 300 < iss.finished_at - iss.tracking_since < 420
    assert cap.status == "done"
