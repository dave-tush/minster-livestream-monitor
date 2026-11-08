"""Crash recovery and playback checkpoint tests for Dominion Radio.

Uses temporary SQLite databases and synthetic audio/track fixtures.
No Telegram credentials, no production database, no live website.
"""

import asyncio
import os
import sqlite3
import tempfile
import time
from datetime import datetime, timedelta, timezone

import pytest

from app.config import Settings
from app.database import Database
from app.radio import RadioEngine
from app.radio import AudioSource


class FakeSource(AudioSource):
    """Fake Telegram source that never connects or downloads."""

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def fetch_since(self, min_id: int):
        return [], min_id

    async def download(self, message_id, dest):
        raise FileNotFoundError("Fake source should never download")


def make_settings(database_url: str, cache_dir: str | None = None) -> Settings:
    return Settings(
        minister_name="Test",
        youtube_api_key="k",
        radio_name="Test Radio",
        telegram_api_id=1,
        telegram_api_hash="hash",
        telegram_session_string="sess",
        telegram_channel="@test",
        database_url=database_url,
        radio_cache_dir=cache_dir or "/tmp/test_radio_cache",
        radio_cache_max_mb=1024,
        radio_sync_interval_seconds=300,
        radio_prefetch_count=2,
    )


def make_engine(database_url: str, mono_clock=None, clock=None,
                checkpoint_interval_seconds: float = 5.0):
    """Create a RadioEngine with a temp DB and fake source."""
    if mono_clock is None:
        mono_clock = time.monotonic
    if clock is None:
        clock = lambda: datetime.now(timezone.utc)
    settings = make_settings(database_url, cache_dir=f"{database_url.split('sqlite:///')[-1]}.cache")
    db = Database(database_url)
    db.init_schema()
    source = FakeSource()
    engine = RadioEngine(settings, db, source, clock=clock,
                          mono_clock=mono_clock,
                          checkpoint_interval_seconds=checkpoint_interval_seconds)
    return engine, db, settings


def make_track(message_id: int, duration: int = 120, title: str = "Test Track",
               performer: str = "Test Artist") -> dict:
    return {
        "message_id": message_id,
        "title": title,
        "performer": performer,
        "duration": duration,
        "file_size": 1024,
        "mime_type": "audio/mpeg",
        "ext": ".mp3",
        "posted_at": "2026-01-01T00:00:00Z",
        "added_at": "2026-01-01T00:00:00Z",
        "available": 1,
    }


# ---------------------------------------------------------------------------
# Database migration tests
# ---------------------------------------------------------------------------


def test_fresh_database_has_new_columns():
    """Fresh DB should have position_seconds and checkpoint_at columns."""
    tmp = tempfile.mktemp(suffix=".db")
    try:
        engine, db, _ = make_engine(database_url=f"sqlite:///{tmp}")
        with db._tx() as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(radio_state)")}
        assert "position_seconds" in cols
        assert "checkpoint_at" in cols
    finally:
        os.unlink(tmp)


def test_migration_adds_columns_to_existing_table():
    """Existing radio_state table without new columns should be migrated."""
    tmp = tempfile.mktemp(suffix=".db")
    try:
        # Create old-style table without new columns
        conn = sqlite3.connect(tmp)
        conn.execute(
            """CREATE TABLE radio_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                message_id INTEGER,
                started_at TEXT,
                last_synced_id INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            "INSERT INTO radio_state (id, updated_at) VALUES (1, '2026-01-01T00:00:00Z')"
        )
        conn.commit()
        conn.close()

        # Now init_schema should add the missing columns
        engine, db, _ = make_engine(database_url=f"sqlite:///{tmp}")
        with db._tx() as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(radio_state)")}
        assert "position_seconds" in cols
        assert "checkpoint_at" in cols
    finally:
        os.unlink(tmp)


def test_migration_is_idempotent():
    """Running init_schema twice should not fail."""
    tmp = tempfile.mktemp(suffix=".db")
    try:
        engine1, db1, _ = make_engine(database_url=f"sqlite:///{tmp}")
        engine2, db2, _ = make_engine(database_url=f"sqlite:///{tmp}")
        # Both should work without error
        with db2._tx() as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(radio_state)")}
        assert "position_seconds" in cols
        assert "checkpoint_at" in cols
    finally:
        os.unlink(tmp)


def test_migration_preserves_existing_state():
    """Existing radio_state data should survive migration."""
    tmp = tempfile.mktemp(suffix=".db")
    try:
        conn = sqlite3.connect(tmp)
        conn.execute(
            """CREATE TABLE radio_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                message_id INTEGER,
                started_at TEXT,
                last_synced_id INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            "INSERT INTO radio_state (id, message_id, started_at, last_synced_id, updated_at) "
            "VALUES (1, 42, '2026-01-01T00:00:00Z', 10, '2026-01-01T00:00:00Z')"
        )
        conn.commit()
        conn.close()

        engine, db, _ = make_engine(database_url=f"sqlite:///{tmp}")
        state = db.radio_get_state()
        assert state["message_id"] == 42
        assert state["started_at"] == "2026-01-01T00:00:00Z"
        assert state["last_synced_id"] == 10
    finally:
        os.unlink(tmp)


# ---------------------------------------------------------------------------
# Startup recovery tests
# ---------------------------------------------------------------------------


def test_startup_with_no_saved_track():
    """Fresh DB with no tracks should return None from now_playing."""
    tmp = tempfile.mktemp(suffix=".db")
    try:
        engine, db, _ = make_engine(database_url=f"sqlite:///{tmp}")
        result = engine.now_playing()
        assert result is None
    finally:
        os.unlink(tmp)


def test_startup_with_saved_checkpoint():
    """Startup should resume from persisted checkpoint position."""
    tmp = tempfile.mktemp(suffix=".db")
    try:
        engine, db, _ = make_engine(database_url=f"sqlite:///{tmp}")
        db.radio_add_tracks([make_track(100, duration=300)], 100)
        now = "2026-01-01T00:00:00Z"
        db.radio_set_state(100, now, position_seconds=45, checkpoint_at=now)

        # Create a new engine (simulating restart) with controlled mono clock
        mono_start = 1000.0
        mono_calls = [mono_start]

        def fake_mono():
            return mono_calls[0]

        engine2, db2, _ = make_engine(
            database_url=f"sqlite:///{tmp}", mono_clock=fake_mono
        )

        # Now playing should resume at position 45 (no time has passed in mono clock)
        result = engine2.now_playing()
        assert result is not None
        assert result["track"]["id"] == 100
        assert result["position"] == 45.0
    finally:
        os.unlink(tmp)


def test_startup_with_missing_track():
    """Startup with saved track that no longer exists should fall back to first track."""
    tmp = tempfile.mktemp(suffix=".db")
    try:
        engine, db, _ = make_engine(database_url=f"sqlite:///{tmp}")
        # Insert track 100 and saved state referencing track 999 (doesn't exist)
        db.radio_add_tracks([make_track(100, duration=300)], 100)
        db.radio_set_state(999, "2026-01-01T00:00:00Z", position_seconds=10,
                           checkpoint_at="2026-01-01T00:00:00Z")

        result = engine.now_playing()
        assert result is not None
        assert result["track"]["id"] == 100  # Fell back to first track
        assert result["position"] == 0.0  # Started fresh
    finally:
        os.unlink(tmp)


def test_startup_with_unavailable_track():
    """Startup with saved track marked unavailable should fall back."""
    tmp = tempfile.mktemp(suffix=".db")
    try:
        engine, db, _ = make_engine(database_url=f"sqlite:///{tmp}")
        db.radio_add_tracks([make_track(100, duration=300)], 100)
        db.radio_set_available(100, False)  # Mark unavailable
        db.radio_set_state(100, "2026-01-01T00:00:00Z", position_seconds=10,
                           checkpoint_at="2026-01-01T00:00:00Z")

        result = engine.now_playing()
        assert result is None  # No available tracks
    finally:
        os.unlink(tmp)


# ---------------------------------------------------------------------------
# Playback progression tests
# ---------------------------------------------------------------------------


def test_playback_progression_uses_monotonic_clock():
    """Position should advance based on monotonic clock, not wall clock."""
    tmp = tempfile.mktemp(suffix=".db")
    try:
        engine, db, _ = make_engine(database_url=f"sqlite:///{tmp}")
        db.radio_add_tracks([make_track(100, duration=300)], 100)

        mono = [1000.0]

        def fake_mono():
            return mono[0]

        engine2, db2, _ = make_engine(
            database_url=f"sqlite:///{tmp}", mono_clock=fake_mono
        )

        # First call initializes checkpoint state from DB (position=0 since none saved)
        result1 = engine2.now_playing()
        assert result1 is not None
        assert result1["position"] == 0.0

        # Advance mono clock by 30 seconds
        mono[0] = 1030.0
        result2 = engine2.now_playing()
        assert result2 is not None
        assert result2["position"] == 30.0  # Advanced by 30s

        # Advance another 20 seconds
        mono[0] = 1050.0
        result3 = engine2.now_playing()
        assert result3 is not None
        assert result3["position"] == 50.0  # Advanced by another 20s
    finally:
        os.unlink(tmp)


def test_server_downtime_excluded_from_playback():
    """After restart, position should not include downtime."""
    tmp = tempfile.mktemp(suffix=".db")
    try:
        engine, db, _ = make_engine(database_url=f"sqlite:///{tmp}")
        db.radio_add_tracks([make_track(100, duration=300)], 100)

        base_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        mono = [1000.0]

        def fake_mono():
            return mono[0]

        def fake_clock():
            return base_time + timedelta(seconds=(mono[0] - 1000.0))

        engine1, db1, _ = make_engine(
            database_url=f"sqlite:///{tmp}", clock=fake_clock, mono_clock=fake_mono
        )

        # First call at mono=1000, position=0
        result1 = engine1.now_playing()
        assert result1 is not None
        assert result1["position"] == 0.0

        # Advance to mono=1030, position should be 30
        mono[0] = 1030.0
        result1 = engine1.now_playing()
        assert result1 is not None
        assert result1["position"] == 30.0

        # Checkpoint explicitly
        engine1._checkpoint()

        # Now simulate restart: new engine instance, new mono clock
        mono2 = [2000.0]

        def fake_mono2():
            return mono2[0]

        def fake_clock2():
            return base_time + timedelta(seconds=(mono2[0] - 1000.0))

        engine2, db2, _ = make_engine(
            database_url=f"sqlite:///{tmp}", clock=fake_clock2, mono_clock=fake_mono2
        )

        # Should resume at position 30, NOT 30 + 600 (downtime excluded)
        result2 = engine2.now_playing()
        assert result2 is not None
        assert result2["position"] == 30.0, f"Expected 30.0, got {result2['position']}"
    finally:
        os.unlink(tmp)


# ---------------------------------------------------------------------------
# Periodic checkpoint tests
# ---------------------------------------------------------------------------


def test_periodic_checkpoint_persists_position():
    """Periodic checkpoint should persist current position."""
    tmp = tempfile.mktemp(suffix=".db")
    try:
        engine, db, _ = make_engine(database_url=f"sqlite:///{tmp}")
        db.radio_add_tracks([make_track(100, duration=300)], 100)

        mono = [1000.0]
        base_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

        def fake_mono():
            return mono[0]

        def fake_clock():
            return base_time + timedelta(seconds=(mono[0] - 1000.0))

        engine2, db2, _ = make_engine(
            database_url=f"sqlite:///{tmp}", clock=fake_clock, mono_clock=fake_mono,
            checkpoint_interval_seconds=5.0
        )

        # Anchor playback at mono=1000 (position 0), then advance mono by 25s
        engine2.now_playing()
        mono[0] = 1025.0
        engine2.now_playing()  # Position should now be 25

        # Manually call checkpoint
        engine2._checkpoint()

        # Verify state was persisted
        state = db2.radio_get_state()
        assert state["message_id"] == 100
        assert state["position_seconds"] == 25
        assert state["checkpoint_at"] is not None
    finally:
        os.unlink(tmp)


# ---------------------------------------------------------------------------
# Graceful shutdown checkpoint tests
# ---------------------------------------------------------------------------


def test_graceful_shutdown_checkpoint():
    """Graceful shutdown should persist final position."""
    tmp = tempfile.mktemp(suffix=".db")
    try:
        engine, db, _ = make_engine(database_url=f"sqlite:///{tmp}")
        db.radio_add_tracks([make_track(100, duration=300)], 100)

        mono = [1000.0]
        base_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

        def fake_mono():
            return mono[0]

        def fake_clock():
            return base_time + timedelta(seconds=(mono[0] - 1000.0))

        engine2, db2, _ = make_engine(
            database_url=f"sqlite:///{tmp}", clock=fake_clock, mono_clock=fake_mono
        )

        # Anchor playback, then advance position by 40s
        engine2.now_playing()
        mono[0] = 1040.0
        engine2.now_playing()

        # Simulate graceful shutdown checkpoint
        engine2._checkpoint()

        state = db2.radio_get_state()
        assert state["position_seconds"] == 40
        assert state["checkpoint_at"] is not None
    finally:
        os.unlink(tmp)


# ---------------------------------------------------------------------------
# Track transition tests
# ---------------------------------------------------------------------------


def test_track_transition_resets_position():
    """Track transition should reset position to 0 and advance atomically."""
    tmp = tempfile.mktemp(suffix=".db")
    try:
        engine, db, _ = make_engine(database_url=f"sqlite:///{tmp}")
        db.radio_add_tracks([
            make_track(100, duration=5, title="Track 100"),
            make_track(200, duration=5, title="Track 200"),
        ], 200)

        mono = [1000.0]

        def fake_mono():
            return mono[0]
        engine2, db2, _ = make_engine(
            database_url=f"sqlite:///{tmp}", mono_clock=fake_mono
        )

        # First call: track 100 at position 0
        result1 = engine2.now_playing()
        assert result1["track"]["id"] == 100
        assert result1["position"] == 0.0

        # Advance past track 100 duration (5s)
        mono[0] = 1006.0
        result2 = engine2.now_playing()
        assert result2 is not None
        assert result2["track"]["id"] == 200
        assert result2["position"] == 1.0  # 1 second into track 200

        # Verify position_seconds was reset for new track
        state = db2.radio_get_state()
        assert state["message_id"] == 200
        assert state["position_seconds"] == 1
    finally:
        os.unlink(tmp)


def test_track_transition_at_exact_boundary():
    """Track ending exactly at duration boundary should advance."""
    tmp = tempfile.mktemp(suffix=".db")
    try:
        engine, db, _ = make_engine(database_url=f"sqlite:///{tmp}")
        db.radio_add_tracks([
            make_track(100, duration=5),
            make_track(200, duration=10),
        ], 200)

        mono = [1000.0]

        def fake_mono():
            return mono[0]
        engine2, db2, _ = make_engine(
            database_url=f"sqlite:///{tmp}", mono_clock=fake_mono
        )

        # Exactly at 10 seconds (track 100 = 5s + track 200 = 5s) - wraps to track 100
        mono[0] = 1010.0
        result = engine2.now_playing()
        assert result["track"]["id"] == 100  # Wrapped back to first
        assert result["position"] == 0.0
    finally:
        os.unlink(tmp)


# ---------------------------------------------------------------------------
# Concurrent access tests
# ---------------------------------------------------------------------------


def test_concurrent_now_playing_requests():
    """Multiple concurrent /radio/now requests should return consistent results."""
    tmp = tempfile.mktemp(suffix=".db")
    try:
        engine, db, _ = make_engine(database_url=f"sqlite:///{tmp}")
        db.radio_add_tracks([make_track(100, duration=300)], 100)

        mono = [1000.0]

        def fake_mono():
            return mono[0]
        engine2, db2, _ = make_engine(
            database_url=f"sqlite:///{tmp}", mono_clock=fake_mono
        )

        results = []
        errors = []

        def call_now():
            try:
                r = engine2.now_playing()
                results.append(r)
            except Exception as e:
                errors.append(e)

        import threading
        threads = [threading.Thread(target=call_now) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Errors: {errors}"
        assert len(results) == 10
        # All should return the same track and position
        for r in results:
            assert r["track"]["id"] == 100
    finally:
        os.unlink(tmp)


# ---------------------------------------------------------------------------
# Missing audio file handling
# ---------------------------------------------------------------------------


def test_missing_audio_file_handling():
    """Track with available=0 should be skipped."""
    tmp = tempfile.mktemp(suffix=".db")
    try:
        engine, db, _ = make_engine(database_url=f"sqlite:///{tmp}")
        db.radio_add_tracks([make_track(100, duration=300)], 100)
        db.radio_set_available(100, False)

        result = engine.now_playing()
        assert result is None  # No available tracks
    finally:
        os.unlink(tmp)


# ---------------------------------------------------------------------------
# Playlist loop behaviour
# ---------------------------------------------------------------------------


def test_playlist_loop_wraps_around():
    """After last track, should wrap to first track."""
    tmp = tempfile.mktemp(suffix=".db")
    try:
        engine, db, _ = make_engine(database_url=f"sqlite:///{tmp}")
        db.radio_add_tracks([
            make_track(100, duration=5),
            make_track(200, duration=5),
        ], 200)

        mono = [1000.0]
        base_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

        def fake_mono():
            return mono[0]

        def fake_clock():
            return base_time + timedelta(seconds=(mono[0] - 1000.0))

        engine2, db2, _ = make_engine(
            database_url=f"sqlite:///{tmp}", clock=fake_clock, mono_clock=fake_mono
        )

        # Anchor playback at mono=1000, then advance past both tracks (10 seconds)
        engine2.now_playing()
        mono[0] = 1011.0
        result = engine2.now_playing()
        assert result["track"]["id"] == 100  # Wrapped back to first
        assert result["position"] == 1.0  # 1 second into track 100
    finally:
        os.unlink(tmp)


# ---------------------------------------------------------------------------
# Public endpoint compatibility
# ---------------------------------------------------------------------------


def test_public_endpoint_returns_expected_fields():
    """/radio/now response should have expected fields for WordPress frontend."""
    tmp = tempfile.mktemp(suffix=".db")
    try:
        engine, db, _ = make_engine(database_url=f"sqlite:///{tmp}")
        db.radio_add_tracks([make_track(100, duration=300, title="My Track",
                                        performer="Test Artist")], 100)

        mono = [1000.0]

        def fake_mono():
            return mono[0]

        engine2, db2, _ = make_engine(
            database_url=f"sqlite:///{tmp}", mono_clock=fake_mono
        )

        result = engine2.now_playing()
        assert result is not None
        # Verify all expected fields are present
        assert "station" in result
        assert "track" in result
        assert "position" in result
        assert "remaining" in result
        assert "started_at" in result
        assert "next" in result
        assert "server_time" in result
        assert "tracks_in_rotation" in result
        assert result["track"]["id"] == 100
        assert result["track"]["title"] == "My Track"
        assert result["track"]["performer"] == "Test Artist"
        assert result["track"]["audio_url"] == "/radio/audio/100"
    finally:
        os.unlink(tmp)


# ---------------------------------------------------------------------------
# Crash/restart around track boundary
# ---------------------------------------------------------------------------


def test_crash_restart_at_track_boundary():
    """Crash right at track boundary should resume on next track at position 0."""
    tmp = tempfile.mktemp(suffix=".db")
    try:
        engine, db, _ = make_engine(database_url=f"sqlite:///{tmp}")
        db.radio_add_tracks([
            make_track(100, duration=5),
            make_track(200, duration=300),
        ], 200)

        base_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        mono = [1000.0]

        def fake_mono():
            return mono[0]

        def fake_clock():
            return base_time + timedelta(seconds=(mono[0] - 1000.0))

        engine1, db1, _ = make_engine(
            database_url=f"sqlite:///{tmp}", clock=fake_clock, mono_clock=fake_mono
        )

        # Anchor playback at mono=1000, then advance exactly to track boundary (5 seconds)
        engine1.now_playing()
        mono[0] = 1005.0
        engine1.now_playing()  # This advances to track 200, position 0
        engine1._checkpoint()  # Persist

        # Simulate crash and restart with new engine
        mono2 = [2000.0]

        def fake_mono2():
            return mono2[0]

        def fake_clock2():
            return base_time + timedelta(seconds=(mono2[0] - 1000.0))

        engine2, db2, _ = make_engine(
            database_url=f"sqlite:///{tmp}", clock=fake_clock2, mono_clock=fake_mono2
        )

        result = engine2.now_playing()
        assert result["track"]["id"] == 200
        assert result["position"] == 0.0
    finally:
        os.unlink(tmp)

# ---------------------------------------------------------------------------
# Ten-minute downtime and forced termination
# ---------------------------------------------------------------------------


def test_ten_minute_downtime_does_not_advance_playback():
    """Ten minutes of server downtime must not advance the on-air position."""
    tmp = tempfile.mktemp(suffix=".db")
    try:
        engine, db, _ = make_engine(database_url=f"sqlite:///{tmp}")
        db.radio_add_tracks([make_track(100, duration=900)], 100)

        base_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        mono = [1000.0]

        def fake_mono():
            return mono[0]

        def fake_clock():
            return base_time + timedelta(seconds=(mono[0] - 1000.0))

        engine1, db1, _ = make_engine(
            database_url=f"sqlite:///{tmp}", clock=fake_clock, mono_clock=fake_mono
        )

        # Anchor playback at mono=1000, advance to position 137, and checkpoint
        engine1.now_playing()
        mono[0] = 1137.0
        result = engine1.now_playing()
        assert result["position"] == 137.0
        engine1._checkpoint()

        # Discard the engine without any shutdown (forced termination), then
        # restart after ten minutes of wall-clock downtime.
        mono2 = [5000.0]
        now2 = base_time + timedelta(minutes=10)

        def fake_mono2():
            return mono2[0]

        def fake_clock2():
            return now2

        engine2, db2, _ = make_engine(
            database_url=f"sqlite:///{tmp}", clock=fake_clock2, mono_clock=fake_mono2
        )

        result2 = engine2.now_playing()
        assert result2 is not None
        assert result2["position"] == 137.0, f"Expected 137.0, got {result2['position']}"
        assert result2["remaining"] == 900.0 - 137.0
    finally:
        os.unlink(tmp)


def test_forced_termination_recovers_checkpoint_without_shutdown():
    """A checkpoint written mid-track must survive a forced kill (no shutdown hook)."""
    tmp = tempfile.mktemp(suffix=".db")
    try:
        engine, db, _ = make_engine(database_url=f"sqlite:///{tmp}")
        db.radio_add_tracks([make_track(100, duration=300),
                             make_track(200, duration=300)], 200)

        base_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        mono = [1000.0]

        def fake_mono():
            return mono[0]

        def fake_clock():
            return base_time + timedelta(seconds=(mono[0] - 1000.0))

        engine1, db1, _ = make_engine(
            database_url=f"sqlite:///{tmp}", clock=fake_clock, mono_clock=fake_mono
        )

        # Anchor, advance, checkpoint at position 137
        engine1.now_playing()
        mono[0] = 1137.0
        engine1.now_playing()
        engine1._checkpoint()

        # No _checkpoint()/shutdown is called here; the engine is simply dropped,
        # as if the process were killed. A new engine must recover from SQLite alone.
        del engine1

        mono2 = [4000.0]

        def fake_mono2():
            return mono2[0]

        engine2, db2, _ = make_engine(
            database_url=f"sqlite:///{tmp}", mono_clock=fake_mono2
        )

        result = engine2.now_playing()
        assert result is not None
        assert result["track"]["id"] == 100
        assert result["position"] == 137.0
    finally:
        os.unlink(tmp)


# ---------------------------------------------------------------------------
# Background loop: checkpoints without listeners, graceful shutdown
# ---------------------------------------------------------------------------


def test_run_forever_checkpoints_and_shuts_down_gracefully():
    """The background loop must checkpoint on its own (no listeners needed) and
    persist a final checkpoint on graceful shutdown."""
    tmp = tempfile.mktemp(suffix=".db")
    try:
        engine, db, _ = make_engine(database_url=f"sqlite:///{tmp}")
        db.radio_add_tracks([make_track(100, duration=300)], 100)
        # Mark the saved tracks as belonging to this channel so sync doesn't reset them
        db.radio_meta_set("channel", "test")

        mono = [1000.0]

        def fake_mono():
            return mono[0]

        engine2, db2, _ = make_engine(
            database_url=f"sqlite:///{tmp}", mono_clock=fake_mono,
            checkpoint_interval_seconds=0.25
        )

        async def scenario():
            stop = asyncio.Event()
            task = asyncio.create_task(engine2.run_forever(stop))
            # Let a few checkpoint iterations run without any website listeners
            await asyncio.sleep(1.0)
            mid = db2.radio_get_state()
            stop.set()
            await asyncio.wait_for(task, timeout=10)
            final = db2.radio_get_state()
            return mid, final

        mid, final = asyncio.run(scenario())

        assert mid["message_id"] == 100
        assert mid["checkpoint_at"] is not None
        assert final["message_id"] == 100
        assert final["checkpoint_at"] is not None
        assert final["updated_at"] is not None
    finally:
        os.unlink(tmp)


# ---------------------------------------------------------------------------
# End-to-end over HTTP: /radio/now recovers the persisted checkpoint
# ---------------------------------------------------------------------------


def test_http_radio_now_recovers_checkpoint():
    """GET /radio/now over HTTP must resume from the checkpoint written before a crash."""
    from fastapi.testclient import TestClient
    from app.main import create_app

    tmp = tempfile.mktemp(suffix=".db")
    try:
        engine, db, settings = make_engine(database_url=f"sqlite:///{tmp}")
        db.radio_add_tracks([make_track(100, duration=300, title="Radio Track",
                                        performer="Test Artist")], 100)
        db.radio_set_state(100, "2026-01-01T00:00:00Z",
                           position_seconds=45, checkpoint_at="2026-01-01T00:00:00Z")

        app = create_app(settings, background=False, radio=engine)
        with TestClient(app) as client:
            r = client.get("/radio/now")

        assert r.status_code == 200
        data = r.json()
        assert data["track"]["id"] == 100
        assert data["track"]["title"] == "Radio Track"
        assert data["track"]["performer"] == "Test Artist"
        assert data["position"] == 45.0
        assert data["remaining"] == 255.0
        assert data["server_time"]
        assert data["started_at"] == "2026-01-01T00:00:00Z"
    finally:
        os.unlink(tmp)

# ---------------------------------------------------------------------------
# Legacy bootstrap tests
# ---------------------------------------------------------------------------


def test_legacy_row_with_available_track_bootstraps_from_position_zero():
    """A legacy radio_state row (checkpoint_at NULL, position_seconds 0) restarts
    the saved track at position 0 without counting downtime."""
    tmp = tempfile.mktemp(suffix=".db")
    try:
        engine, db, _ = make_engine(database_url=f"sqlite:///{tmp}")
        db.radio_add_tracks([make_track(100, duration=300)], 100)
        # Legacy row: valid message_id, started_at set, but no checkpoint data
        db.radio_set_state(100, "2026-01-01T00:00:00Z",
                           position_seconds=0, checkpoint_at=None)

        # First call bootstraps the row deterministically
        base = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        mono = [1000.0]

        def fake_clock():
            return base + timedelta(seconds=(mono[0] - 1000.0))

        def fake_mono():
            return mono[0]

        engine2, db2, _ = make_engine(
            database_url=f"sqlite:///{tmp}", clock=fake_clock, mono_clock=fake_mono
        )

        result = engine2.now_playing()
        assert result is not None
        assert result["track"]["id"] == 100
        assert result["position"] == 0.0
        assert result["started_at"] == "2026-06-01T12:00:00Z"

        # Bootstrap must have persisted immediately and atomically
        state = db2.radio_get_state()
        assert state["message_id"] == 100
        assert state["position_seconds"] == 0
        assert state["checkpoint_at"] == "2026-06-01T12:00:00Z"

        # Second call (no further mono advance) must NOT reset position again
        result2 = engine2.now_playing()
        assert result2["position"] == 0.0
        assert result2["started_at"] == "2026-06-01T12:00:00Z"
    finally:
        os.unlink(tmp)


def test_legacy_row_repeated_restart_does_not_reset_position():
    """After bootstrapping once, subsequent restarts resume from the saved
    checkpoint position and do not restart from position zero repeatedly."""
    tmp = tempfile.mktemp(suffix=".db")
    try:
        engine, db, _ = make_engine(database_url=f"sqlite:///{tmp}")
        db.radio_add_tracks([make_track(100, duration=300)], 100)
        # Legacy row
        db.radio_set_state(100, "2026-01-01T00:00:00Z",
                           position_seconds=0, checkpoint_at=None)

        base = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        mono = [1000.0]

        def fake_clock():
            return base + timedelta(seconds=(mono[0] - 1000.0))

        def fake_mono():
            return mono[0]

        # Engine 1: bootstrap at position 0, advance to 37, checkpoint
        engine1, db1, _ = make_engine(
            database_url=f"sqlite:///{tmp}", clock=fake_clock, mono_clock=fake_mono
        )
        engine1.now_playing()
        mono[0] = 1037.0
        engine1.now_playing()
        engine1._checkpoint()

        # Engine 2: new process, resume at 37 (not reset to 0)
        mono2 = [5000.0]
        now2 = base + timedelta(minutes=10)

        def fake_clock2():
            return now2

        def fake_mono2():
            return mono2[0]

        engine2, db2, _ = make_engine(
            database_url=f"sqlite:///{tmp}", clock=fake_clock2, mono_clock=fake_mono2
        )
        result = engine2.now_playing()
        assert result["position"] == 37.0

        # Engine 3: yet another restart still returns 37, not 0
        engine3, db3, _ = make_engine(
            database_url=f"sqlite:///{tmp}", clock=fake_clock2, mono_clock=fake_mono2
        )
        result3 = engine3.now_playing()
        assert result3["position"] == 37.0
    finally:
        os.unlink(tmp)


def test_legacy_row_with_missing_track_falls_back_to_first_available():
    """A legacy row whose saved track no longer exists uses the safe fallback."""
    tmp = tempfile.mktemp(suffix=".db")
    try:
        engine, db, _ = make_engine(database_url=f"sqlite:///{tmp}")
        db.radio_add_tracks([make_track(100, duration=300)], 100)
        # Legacy row referencing message_id 999 which does not exist
        db.radio_set_state(999, "2026-01-01T00:00:00Z",
                           position_seconds=42, checkpoint_at=None)

        result = engine.now_playing()
        assert result is not None
        assert result["track"]["id"] == 100  # fell back to first available track
        assert result["position"] == 0.0  # started fresh, not from 42

        # The fallback state is checkpointed
        state = db.radio_get_state()
        assert state["message_id"] == 100
        assert state["position_seconds"] == 0
        assert state["checkpoint_at"] is not None
    finally:
        os.unlink(tmp)


def test_legacy_row_with_unavailable_track_skips_it():
    """A legacy row whose saved track is unavailable skips to another available track."""
    tmp = tempfile.mktemp(suffix=".db")
    try:
        engine, db, _ = make_engine(database_url=f"sqlite:///{tmp}")
        db.radio_add_tracks([
            make_track(100, duration=300, title="Unavailable"),
            make_track(200, duration=300, title="Available"),
        ], 200)
        db.radio_set_available(100, False)
        db.radio_set_state(100, "2026-01-01T00:00:00Z",
                           position_seconds=10, checkpoint_at=None)

        result = engine.now_playing()
        assert result is not None
        assert result["track"]["id"] == 200  # track 100 skipped
        assert result["position"] == 0.0
    finally:
        os.unlink(tmp)


def test_legacy_row_with_empty_rotation_returns_none():
    """A legacy row with no available tracks yields no on-air position."""
    tmp = tempfile.mktemp(suffix=".db")
    try:
        engine, db, _ = make_engine(database_url=f"sqlite:///{tmp}")
        # Legacy row but no tracks at all
        db.radio_set_state(100, "2026-01-01T00:00:00Z",
                           position_seconds=0, checkpoint_at=None)

        result = engine.now_playing()
        assert result is None
    finally:
        os.unlink(tmp)


def test_existing_checkpointed_record_still_restores_saved_position():
    """Records that already have checkpoint_at continue to resume from their
    saved position; they are not bootstrapped again."""
    tmp = tempfile.mktemp(suffix=".db")
    try:
        engine, db, _ = make_engine(database_url=f"sqlite:///{tmp}")
        db.radio_add_tracks([make_track(100, duration=300)], 100)
        # Already checkpointed record
        db.radio_set_state(100, "2026-01-01T00:00:00Z",
                           position_seconds=90, checkpoint_at="2026-01-01T00:00:00Z")

        base = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        mono = [1000.0]

        def fake_clock():
            return base + timedelta(seconds=(mono[0] - 1000.0))

        def fake_mono():
            return mono[0]

        engine2, db2, _ = make_engine(
            database_url=f"sqlite:///{tmp}", clock=fake_clock, mono_clock=fake_mono
        )
        result = engine2.now_playing()
        assert result["position"] == 90.0  # restored from checkpoint, not 0

        # Advance mono by 15 s; position should now be 105
        mono[0] = 1015.0
        result2 = engine2.now_playing()
        assert result2["position"] == 105.0
    finally:
        os.unlink(tmp)


# ---------------------------------------------------------------------------
# Channel-marker tests
# ---------------------------------------------------------------------------


def test_existing_tracks_survive_missing_channel_marker():
    """When radio_meta has no channel marker but tracks exist, _ensure_channel
    adopts them under the current channel instead of deleting them."""
    tmp = tempfile.mktemp(suffix=".db")
    try:
        engine, db, _ = make_engine(database_url=f"sqlite:///{tmp}")
        db.radio_add_tracks([make_track(100, duration=300)], 100)
        # Confirm no channel marker is set yet
        assert db.radio_meta_get("channel") is None
        assert db.radio_count() == 1

        # _ensure_channel must not delete tracks when no prior channel is known
        engine._ensure_channel()
        assert db.radio_meta_get("channel") == "test"
        assert db.radio_count() == 1
        assert db.radio_get_track(100) is not None
    finally:
        os.unlink(tmp)


def test_channel_marker_initialization_preserves_radio_state():
    """Existing playback state and checkpoint data survive missing-marker
    initialization."""
    tmp = tempfile.mktemp(suffix=".db")
    try:
        engine, db, _ = make_engine(database_url=f"sqlite:///{tmp}")
        db.radio_add_tracks([make_track(100, duration=300)], 100)
        # Legacy state with checkpoint data
        db.radio_set_state(100, "2026-01-01T00:00:00Z",
                           position_seconds=55, checkpoint_at="2026-01-01T00:00:00Z")
        assert db.radio_meta_get("channel") is None

        engine._ensure_channel()

        assert db.radio_meta_get("channel") == "test"
        # Playback state untouched
        state = db.radio_get_state()
        assert state["message_id"] == 100
        assert state["position_seconds"] == 55
        assert state["checkpoint_at"] == "2026-01-01T00:00:00Z"
    finally:
        os.unlink(tmp)


def test_genuine_channel_change_still_resets_playlist():
    """When the channel marker is present and differs, existing behaviour resets
    tracks and radio state."""
    tmp = tempfile.mktemp(suffix=".db")
    try:
        engine, db, _ = make_engine(database_url=f"sqlite:///{tmp}")
        db.radio_add_tracks([make_track(100, duration=300)], 100)
        db.radio_meta_set("channel", "old-channel")
        db.radio_set_state(100, "2026-01-01T00:00:00Z",
                           position_seconds=55, checkpoint_at="2026-01-01T00:00:00Z")

        # Switch the saved marker to a different channel, then call _ensure_channel
        # on the engine whose configured channel is 'test'
        db.radio_meta_set("channel", "different-channel")
        engine._ensure_channel()

        assert db.radio_meta_get("channel") == "test"  # back to configured channel after reset+set
        assert db.radio_count() == 0  # playlist cleared
    finally:
        os.unlink(tmp)


def test_missing_channel_marker_no_tracks_does_not_reset():
    """No channel marker and no tracks: marker is simply set, no reset occurs."""
    tmp = tempfile.mktemp(suffix=".db")
    try:
        engine, db, _ = make_engine(database_url=f"sqlite:///{tmp}")
        assert db.radio_meta_get("channel") is None
        assert not db.radio_has_tracks()

        engine._ensure_channel()
        assert db.radio_meta_get("channel") == "test"
    finally:
        os.unlink(tmp)


# ---------------------------------------------------------------------------
# Cancellation and concurrency tests for run_forever()
# ---------------------------------------------------------------------------


def test_run_forever_task_cancellation_during_loop_wait():
    """Cancelling the background task while it waits in the main loop exits
    cleanly and runs the final checkpoint."""
    tmp = tempfile.mktemp(suffix=".db")
    try:
        engine, db, _ = make_engine(database_url=f"sqlite:///{tmp}")
        db.radio_add_tracks([make_track(100, duration=300)], 100)
        db.radio_meta_set("channel", "test")

        mono = [1000.0]

        def fake_mono():
            return mono[0]

        engine2, db2, _ = make_engine(
            database_url=f"sqlite:///{tmp}", mono_clock=fake_mono,
            checkpoint_interval_seconds=0.25
        )

        async def scenario():
            stop = asyncio.Event()
            task = asyncio.create_task(engine2.run_forever(stop))
            # Let at least one checkpoint fire
            await asyncio.sleep(0.6)
            mid = db2.radio_get_state()
            # Cancel instead of stop
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            return mid

        mid = asyncio.run(scenario())
        assert mid["message_id"] == 100
        assert mid["checkpoint_at"] is not None
        # Final state persists from cancellation path
        final = db2.radio_get_state()
        assert final["checkpoint_at"] is not None
    finally:
        os.unlink(tmp)


def test_run_forever_cancellation_does_not_swallow_in_flight_error():
    """An exception already in flight is not replaced by a checkpoint error
    during shutdown."""
    tmp = tempfile.mktemp(suffix=".db")
    try:
        engine, db, _ = make_engine(database_url=f"sqlite:///{tmp}")
        db.radio_add_tracks([make_track(100, duration=300)], 100)
        db.radio_meta_set("channel", "test")

        # Make connect() always raise so an error is in flight
        class FailingSource:
            async def connect(self):
                raise RuntimeError("boom")

            async def disconnect(self):
                pass

            async def fetch_since(self, min_id):
                return [], min_id

            async def download(self, message_id, dest):
                raise FileNotFoundError("no")

        engine2, db2, _ = make_engine(database_url=f"sqlite:///{tmp}")
        engine2.source = FailingSource()

        mono = [1000.0]

        def fake_mono():
            return mono[0]

        engine2._mono_clock = fake_mono

        async def scenario():
            stop = asyncio.Event()
            task = asyncio.create_task(engine2.run_forever(stop))
            # Give the loop time to enter the error path
            await asyncio.sleep(0.4)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(scenario())
        # The source connection error, not a checkpoint error, is what fails
        assert engine2.last_error is not None and "boom" in engine2.last_error
    finally:
        os.unlink(tmp)


def test_run_forever_final_checkpoint_does_not_overwrite_newer_transition():
    """The shutdown checkpoint uses the current (possibly advanced) state, not
    a stale position from an earlier checkpoint."""
    tmp = tempfile.mktemp(suffix=".db")
    try:
        engine, db, _ = make_engine(database_url=f"sqlite:///{tmp}")
        db.radio_add_tracks([
            make_track(100, duration=5),
            make_track(200, duration=300),
        ], 200)

        base = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        mono = [1000.0]

        def fake_clock():
            return base + timedelta(seconds=(mono[0] - 1000.0))

        def fake_mono():
            return mono[0]

        engine2, db2, _ = make_engine(
            database_url=f"sqlite:///{tmp}", clock=fake_clock, mono_clock=fake_mono
        )

        # Advance past track 100 (5 s) onto track 200 at position 1, then checkpoint
        engine2.now_playing()
        mono[0] = 1006.0
        result = engine2.now_playing()
        assert result["track"]["id"] == 200
        assert result["position"] == 1.0
        engine2._checkpoint()

        # Simulate shutdown: final checkpoint must preserve track 200 at position 1,
        # not revert to track 100
        engine2._checkpoint()
        state = db2.radio_get_state()
        assert state["message_id"] == 200
        assert state["position_seconds"] == 1
    finally:
        os.unlink(tmp)


def test_concurrent_now_playing_and_checkpoint_preserve_consistency():
    """Concurrent now_playing() calls plus a checkpoint do not interleave to
    produce inconsistent state (position_seconds matches returned position)."""
    tmp = tempfile.mktemp(suffix=".db")
    try:
        engine, db, _ = make_engine(database_url=f"sqlite:///{tmp}")
        db.radio_add_tracks([make_track(100, duration=300)], 100)

        base = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        mono = [1000.0]

        def fake_clock():
            return base + timedelta(seconds=(mono[0] - 1000.0))

        def fake_mono():
            return mono[0]

        engine2, db2, _ = make_engine(
            database_url=f"sqlite:///{tmp}", clock=fake_clock, mono_clock=fake_mono
        )

        # Anchor playback at mono=1000 (position 0) before concurrent access
        engine2.now_playing()
        mono[0] = 1025.0

        results = []
        errors = []

        def read_now():
            try:
                r = engine2.now_playing()
                results.append(r)
            except Exception as exc:
                errors.append(exc)

        import threading
        threads = [threading.Thread(target=read_now) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, errors
        assert len(results) == 8
        # All calls see the same consistent state
        for r in results:
            assert r["track"]["id"] == 100
            assert r["position"] == 25.0

        # Persist one checkpoint and verify the stored position matches callers
        engine2._checkpoint()
        state = db2.radio_get_state()
        assert state["position_seconds"] == 25
    finally:
        os.unlink(tmp)



# ---------------------------------------------------------------------------
# Fractional playback position
# ---------------------------------------------------------------------------


def test_checkpoint_preserves_fractional_position():
    """Checkpointed fractional positions survive a restart without truncation."""
    tmp = tempfile.mktemp(suffix=".db")
    try:
        engine, db, _ = make_engine(database_url=f"sqlite:///{tmp}")
        db.radio_add_tracks([make_track(100, duration=300)], 100)

        base = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        mono = [1000.0]

        def fake_mono():
            return mono[0]

        def fake_clock():
            return base + timedelta(seconds=(mono[0] - 1000.0))

        engine2, db2, _ = make_engine(
            database_url=f"sqlite:///{tmp}", clock=fake_clock, mono_clock=fake_mono
        )
        engine2.now_playing()
        # Position 37.75s
        mono[0] = 1037.75
        engine2._checkpoint()

        value = db2.radio_get_state()["position_seconds"]
        assert abs(value - 37.75) < 1e-6

        # New engine restores exactly the fractional position
        mono2 = [5000.0]

        def fake_mono2():
            return mono2[0]

        def fake_clock2():
            return base + timedelta(minutes=10)

        engine3, db3, _ = make_engine(
            database_url=f"sqlite:///{tmp}", clock=fake_clock2, mono_clock=fake_mono2
        )
        result = engine3.now_playing()
        assert abs(result["position"] - 37.75) < 1e-6
    finally:
        os.unlink(tmp)


def test_track_transition_preserves_fractional_remainder():
    """Overflowing a track boundary keeps the fractional remainder."""
    tmp = tempfile.mktemp(suffix=".db")
    try:
        engine, db, _ = make_engine(database_url=f"sqlite:///{tmp}")
        db.radio_add_tracks([
            make_track(100, duration=5),
            make_track(200, duration=300),
        ], 200)

        mono = [1000.0]
        base = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

        def fake_mono():
            return mono[0]

        def fake_clock():
            return base + timedelta(seconds=(mono[0] - 1000.0))

        engine2, db2, _ = make_engine(
            database_url=f"sqlite:///{tmp}", clock=fake_clock, mono_clock=fake_mono
        )
        engine2.now_playing()
        # Track 100 (5s) ends at 5.0; at 5.25s we're at 0.25s into track 200
        mono[0] = 1005.25
        result = engine2.now_playing()
        assert result["track"]["id"] == 200
        assert abs(result["position"] - 0.25) < 1e-6
    finally:
        os.unlink(tmp)


def test_legacy_integer_position_still_restores():
    """Old integer-only databases restore fine with the fractional-capable schema."""
    tmp = tempfile.mktemp(suffix=".db")
    try:
        # Simulate a pre-fractional database with round integers
        conn = sqlite3.connect(tmp)
        conn.executescript(
            """
            CREATE TABLE radio_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                message_id INTEGER,
                started_at TEXT,
                last_synced_id INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                position_seconds INTEGER NOT NULL DEFAULT 0,
                checkpoint_at TEXT
            )
            """)
        conn.commit()
        conn.close()
        engine, db, _ = make_engine(database_url=f"sqlite:///{tmp}")
        db.radio_add_tracks([make_track(100, duration=300)], 100)
        db.radio_set_state(100, "2026-01-01T00:00:00Z",
                           position_seconds=10, checkpoint_at="2026-01-01T00:00:00Z")

        base = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        mono = [1000.0]

        def fake_mono():
            return mono[0]

        def fake_clock():
            return base + timedelta(seconds=(mono[0] - 1000.0))

        engine2, db2, _ = make_engine(
            database_url=f"sqlite:///{tmp}", clock=fake_clock, mono_clock=fake_mono
        )
        result = engine2.now_playing()
        assert result["position"] == 10.0
    finally:
        os.unlink(tmp)
