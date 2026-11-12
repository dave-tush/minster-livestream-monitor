"""Next-sermon advance, real-duration correction, batched sync, restart resume, watchdog."""
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.keepalive import KeepAliveStatus, Watchdog
from app.radio import RadioEngine, TrackMeta
from tests.test_radio_recovery import make_engine, make_settings


def meta(i, dur=100):
    return TrackMeta(message_id=i, title=f"T{i}", duration=dur, posted_at="2026-01-01T00:00:00Z")


class Clock:
    def __init__(self):
        self.t = 1000.0
    def __call__(self):
        return self.t


def test_ended_hint_moves_to_next_track(tmp_path):
    mono = Clock()
    eng, db, _ = make_engine(f"sqlite:///{tmp_path}/a.db", mono_clock=mono)
    db.radio_add_tracks([meta(1).as_row(), meta(2).as_row()], 2)
    assert eng.now_playing()["track"]["id"] == 1
    mono.t += 92                                   # client's real file ended; station is 8s behind
    assert eng.now_playing()["track"]["id"] == 1
    assert eng.now_playing(ended_id=1)["track"]["id"] == 2


def test_ended_hint_cannot_skip_early(tmp_path):
    mono = Clock()
    eng, db, _ = make_engine(f"sqlite:///{tmp_path}/b.db", mono_clock=mono)
    db.radio_add_tracks([meta(1, 1000).as_row(), meta(2).as_row()], 2)
    eng.now_playing()
    mono.t += 100
    assert eng.now_playing(ended_id=1)["track"]["id"] == 1   # 900s left: ignored


def test_real_duration_replaces_telegram_estimate(tmp_path, monkeypatch):
    eng, db, _ = make_engine(f"sqlite:///{tmp_path}/c.db")
    db.radio_add_tracks([meta(1, 500).as_row()], 1)
    monkeypatch.setattr("app.radio.probe_duration", lambda p: 120)
    asyncio.run(eng._use_real_duration(db.radio_get_track(1), Path("x")))
    assert db.radio_get_track(1)["duration"] == 120


def test_sync_reads_channel_in_batches(tmp_path):
    eng, db, _ = make_engine(f"sqlite:///{tmp_path}/d.db")
    calls = []

    class Src:
        async def fetch_since(self, min_id, limit=None):
            calls.append(min_id)
            ids = [i for i in range(min_id + 1, 8) ][:3]
            return [meta(i) for i in ids], (ids[-1] if ids else min_id)
    eng.source = Src()
    assert asyncio.run(eng.sync_once()) == 7
    assert len(calls) == 4                         # 3 + 3 + 1 + one empty call that ends it


def test_station_resumes_from_telegram_snapshot(tmp_path):
    eng, db, _ = make_engine(f"sqlite:///{tmp_path}/e.db")
    saved = {}

    class Src:
        async def fetch_since(self, min_id, limit=None):
            return [], min_id
        async def save_snapshot(self, text):
            saved["t"] = text
        async def load_snapshot(self):
            return saved.get("t")
        async def fetch_track(self, mid):
            return meta(mid, 3600)
    eng.source = Src()
    db.radio_add_tracks([meta(1, 3600).as_row(), meta(2, 3600).as_row(), meta(3, 3600).as_row()], 3)
    db.radio_set_state(3, "2026-01-01T00:00:00Z", position_seconds=1500,
                       checkpoint_at="2026-01-01T00:00:00Z")
    eng._init_checkpoint_state()
    asyncio.run(eng.save_snapshot())

    # Render restarts: brand-new empty database
    eng2, db2, _ = make_engine(f"sqlite:///{tmp_path}/fresh.db")
    eng2.source = Src()
    assert asyncio.run(eng2.restore_from_snapshot()) is True
    now = eng2.now_playing()
    assert now["track"]["id"] == 3 and 1499 <= now["position"] <= 1502


def test_watchdog_restarts_a_dead_task():
    async def go():
        stop = asyncio.Event()
        runs = []

        async def flaky():
            runs.append(1)
            if len(runs) == 1:
                raise RuntimeError("boom")
            await stop.wait()
        dog = Watchdog(stop, KeepAliveStatus("", 240), check_seconds=0.05)
        dog.start("radio", flaky)
        t = asyncio.create_task(dog.run())
        await asyncio.sleep(0.3)
        stop.set()
        await t
        await dog.cancel_all()
        return len(runs), dog.status.restarts
    runs, restarts = asyncio.run(go())
    assert runs == 2 and restarts == {"radio": 1}
