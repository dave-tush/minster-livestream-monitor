"""Stage 2: SQLite persistence.

Two tables:
  livestreams    one row per YouTube video ever detected (video_id is UNIQUE -> no duplicates)
  current_state  a single row (id = 1) saying what is live right now

Portability: all SQL lives in this file, uses standard syntax, and every method takes/returns
plain dicts. To move to PostgreSQL later, reimplement `Database` (swap `?` for `%s`, use SERIAL
instead of AUTOINCREMENT); nothing else in the app needs to change.
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .models import LiveVideo

SCHEMA = """
CREATE TABLE IF NOT EXISTS livestreams (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    minister_name   TEXT    NOT NULL,
    video_id        TEXT    NOT NULL UNIQUE,
    youtube_url     TEXT    NOT NULL,
    channel_id      TEXT    NOT NULL,
    channel_title   TEXT    NOT NULL,
    title           TEXT    NOT NULL,
    thumbnail_url   TEXT,
    status          TEXT    NOT NULL,          -- 'live' | 'ended'
    started_at      TEXT,                      -- from YouTube (ISO 8601 UTC)
    detected_at     TEXT    NOT NULL,          -- when we first saw it
    ended_at        TEXT,
    last_checked_at TEXT    NOT NULL,
    match_score     INTEGER NOT NULL DEFAULT 0,
    match_reasons   TEXT    NOT NULL DEFAULT '[]'   -- JSON list, for debugging
);
CREATE INDEX IF NOT EXISTS idx_livestreams_status ON livestreams (status);

CREATE TABLE IF NOT EXISTS current_state (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    status     TEXT NOT NULL,                  -- 'live' | 'offline'
    video_id   TEXT,
    updated_at TEXT NOT NULL
);

-- What the transcript says about when the minister comes up to minister (one row per stream).
CREATE TABLE IF NOT EXISTS transcript_checks (
    video_id              TEXT PRIMARY KEY,
    status                TEXT    NOT NULL,      -- none|mentioned|announced|welcomed|introduced|ministering
    timestamp_seconds     INTEGER,               -- where to jump in the video
    first_mention_seconds INTEGER,
    evidence              TEXT    NOT NULL DEFAULT '',
    announced_time        TEXT,
    cues                  TEXT    NOT NULL DEFAULT '[]',
    source_status         TEXT    NOT NULL,      -- ok|no_captions|blocked|unavailable|error
    checks                INTEGER NOT NULL DEFAULT 0,
    last_checked_at       TEXT    NOT NULL,
    found_at              TEXT
);

-- Dominion Radio: one row per audio post in the Telegram channel (message_id is the
-- channel's own increasing post number, so ORDER BY message_id = oldest upload first).
CREATE TABLE IF NOT EXISTS radio_tracks (
    message_id INTEGER PRIMARY KEY,
    title      TEXT    NOT NULL,
    performer  TEXT    NOT NULL DEFAULT '',
    duration   INTEGER NOT NULL,                -- seconds
    file_size  INTEGER NOT NULL DEFAULT 0,
    mime_type  TEXT    NOT NULL DEFAULT 'audio/mpeg',
    ext        TEXT    NOT NULL DEFAULT '.mp3',
    posted_at  TEXT,
    added_at   TEXT    NOT NULL,
    available  INTEGER NOT NULL DEFAULT 1       -- 0 = download failed / removed, skipped
);

-- Small key/value notes for the radio (e.g. which Telegram channel the saved tracks came from).
CREATE TABLE IF NOT EXISTS radio_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Single row: which track is on air, when it started, and how far we have synced the channel.
CREATE TABLE IF NOT EXISTS radio_state (
    id             INTEGER PRIMARY KEY CHECK (id = 1),
    message_id     INTEGER,
    started_at     TEXT,
    last_synced_id INTEGER NOT NULL DEFAULT 0,
    updated_at     TEXT NOT NULL
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Database:
    def __init__(self, database_url: str):
        prefix = "sqlite:///"
        if not database_url.startswith(prefix):
            raise ValueError("Only sqlite:/// URLs are supported for now")
        self.path = database_url[len(prefix):]
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _tx(self):
        """One short-lived connection per operation: safe across threads, commits atomically."""
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self._tx() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            if conn.execute("SELECT 1 FROM current_state WHERE id = 1").fetchone() is None:
                conn.execute("INSERT INTO current_state (id, status, video_id, updated_at) "
                             "VALUES (1, 'offline', NULL, ?)", (utcnow(),))
            if conn.execute("SELECT 1 FROM radio_state WHERE id = 1").fetchone() is None:
                conn.execute("INSERT INTO radio_state (id, updated_at) VALUES (1, ?)", (utcnow(),))

    # ---- writes ----------------------------------------------------------
    def record_live(self, video: LiveVideo, minister_name: str,
                    match_score: int = 0, match_reasons: list[str] | None = None) -> tuple[dict, bool]:
        """Insert the stream if new, otherwise refresh it. Marks it as the current stream.

        Returns (row, is_new). is_new is True only the first time this video_id is seen.
        """
        now = utcnow()
        reasons = json.dumps(match_reasons or [])
        with self._tx() as conn:
            existing = conn.execute("SELECT id FROM livestreams WHERE video_id = ?",
                                    (video.video_id,)).fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO livestreams (minister_name, video_id, youtube_url, channel_id, "
                    "channel_title, title, thumbnail_url, status, started_at, detected_at, "
                    "last_checked_at, match_score, match_reasons) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'live', ?, ?, ?, ?, ?)",
                    (minister_name, video.video_id, video.youtube_url, video.channel_id,
                     video.channel_title, video.title, video.thumbnail_url, video.started_at,
                     now, now, match_score, reasons))
            else:
                # Refresh title etc. (hosts often rename streams); reopen if it had been marked ended.
                conn.execute(
                    "UPDATE livestreams SET title = ?, channel_title = ?, thumbnail_url = ?, "
                    "status = 'live', ended_at = NULL, last_checked_at = ?, match_score = ?, "
                    "match_reasons = ? WHERE video_id = ?",
                    (video.title, video.channel_title, video.thumbnail_url, now,
                     match_score, reasons, video.video_id))
            conn.execute("UPDATE current_state SET status = 'live', video_id = ?, updated_at = ? "
                         "WHERE id = 1", (video.video_id, now))
            row = conn.execute("SELECT * FROM livestreams WHERE video_id = ?",
                               (video.video_id,)).fetchone()
        return dict(row), existing is None

    def mark_ended(self, video_id: str) -> bool:
        """Mark a stream ended. Clears current state only if this was the current stream.

        Returns True if a live stream was actually changed to ended.
        """
        now = utcnow()
        with self._tx() as conn:
            cur = conn.execute("UPDATE livestreams SET status = 'ended', ended_at = ?, "
                               "last_checked_at = ? WHERE video_id = ? AND status = 'live'",
                               (now, now, video_id))
            conn.execute("UPDATE current_state SET status = 'offline', video_id = NULL, "
                         "updated_at = ? WHERE id = 1 AND video_id = ?", (now, video_id))
            return cur.rowcount > 0

    def touch(self, video_id: str) -> None:
        """Record that we re-verified a stream and it is still live."""
        with self._tx() as conn:
            conn.execute("UPDATE livestreams SET last_checked_at = ? WHERE video_id = ?",
                         (utcnow(), video_id))

    # ---- reads -----------------------------------------------------------
    def get_current(self) -> dict | None:
        """The live stream right now, or None if offline."""
        with self._tx() as conn:
            row = conn.execute(
                "SELECT l.* FROM current_state s JOIN livestreams l ON l.video_id = s.video_id "
                "WHERE s.id = 1 AND s.status = 'live'").fetchone()
        return dict(row) if row else None

    def get_livestream(self, video_id: str) -> dict | None:
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM livestreams WHERE video_id = ?", (video_id,)).fetchone()
        return dict(row) if row else None

    def list_recent(self, limit: int = 20) -> list[dict]:
        with self._tx() as conn:
            rows = conn.execute("SELECT * FROM livestreams ORDER BY detected_at DESC, id DESC LIMIT ?",
                                (limit,)).fetchall()
        return [dict(r) for r in rows]


    # ---- Dominion Radio ----------------------------------------------------
    def radio_add_tracks(self, tracks: list[dict], last_synced_id: int) -> int:
        """Insert new tracks (ignores ones we already have) and move the sync cursor. Returns #added."""
        now = utcnow()
        added = 0
        with self._tx() as conn:
            for t in tracks:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO radio_tracks (message_id, title, performer, duration, "
                    "file_size, mime_type, ext, posted_at, added_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (t["message_id"], t["title"], t.get("performer", ""), t["duration"],
                     t.get("file_size", 0), t.get("mime_type", "audio/mpeg"), t.get("ext", ".mp3"),
                     t.get("posted_at"), now))
                added += cur.rowcount
            conn.execute("UPDATE radio_state SET last_synced_id = MAX(last_synced_id, ?), updated_at = ? "
                         "WHERE id = 1", (last_synced_id, now))
        return added

    def radio_get_state(self) -> dict:
        with self._tx() as conn:
            return dict(conn.execute("SELECT * FROM radio_state WHERE id = 1").fetchone())

    def radio_set_state(self, message_id: int | None, started_at: str | None) -> None:
        with self._tx() as conn:
            conn.execute("UPDATE radio_state SET message_id = ?, started_at = ?, updated_at = ? "
                         "WHERE id = 1", (message_id, started_at, utcnow()))

    def radio_get_track(self, message_id: int) -> dict | None:
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM radio_tracks WHERE message_id = ?", (message_id,)).fetchone()
        return dict(row) if row else None

    def radio_first_track(self) -> dict | None:
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM radio_tracks WHERE available = 1 "
                               "ORDER BY message_id LIMIT 1").fetchone()
        return dict(row) if row else None

    def radio_next_track(self, after_message_id: int) -> dict | None:
        """The track after `after_message_id`, wrapping back to the first one at the end."""
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM radio_tracks WHERE available = 1 AND message_id > ? "
                               "ORDER BY message_id LIMIT 1", (after_message_id,)).fetchone()
            if row is None:
                row = conn.execute("SELECT * FROM radio_tracks WHERE available = 1 "
                                   "ORDER BY message_id LIMIT 1").fetchone()
        return dict(row) if row else None

    def radio_total_seconds(self) -> int:
        with self._tx() as conn:
            return conn.execute("SELECT COALESCE(SUM(duration), 0) FROM radio_tracks "
                                "WHERE available = 1").fetchone()[0]

    def radio_set_available(self, message_id: int, available: bool) -> None:
        with self._tx() as conn:
            conn.execute("UPDATE radio_tracks SET available = ? WHERE message_id = ?",
                         (1 if available else 0, message_id))

    def radio_list_tracks(self, limit: int = 100, offset: int = 0) -> list[dict]:
        with self._tx() as conn:
            rows = conn.execute("SELECT * FROM radio_tracks WHERE available = 1 "
                                "ORDER BY message_id LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        return [dict(r) for r in rows]

    def radio_count(self) -> int:
        with self._tx() as conn:
            return conn.execute("SELECT COUNT(*) FROM radio_tracks WHERE available = 1").fetchone()[0]

    # ---- transcript / ministration timing ------------------------------------
    def transcript_get(self, video_id: str) -> dict | None:
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM transcript_checks WHERE video_id = ?", (video_id,)).fetchone()
        if row is None:
            return None
        out = dict(row)
        out["cues"] = json.loads(out["cues"] or "[]")
        return out

    def transcript_save(self, video_id: str, status: str, source_status: str, *,
                        timestamp_seconds=None, first_mention_seconds=None, evidence="",
                        announced_time=None, cues=None, keep_findings: bool = False) -> None:
        """Upsert a check. keep_findings=True only counts the attempt (used when a later check found less)."""
        now = utcnow()
        with self._tx() as conn:
            exists = conn.execute("SELECT 1 FROM transcript_checks WHERE video_id = ?", (video_id,)).fetchone()
            if exists and keep_findings:
                conn.execute("UPDATE transcript_checks SET checks = checks + 1, last_checked_at = ?, "
                             "source_status = ? WHERE video_id = ?", (now, source_status, video_id))
            elif exists:
                conn.execute(
                    "UPDATE transcript_checks SET status = ?, timestamp_seconds = ?, first_mention_seconds = ?, "
                    "evidence = ?, announced_time = ?, cues = ?, source_status = ?, checks = checks + 1, "
                    "last_checked_at = ?, found_at = ? WHERE video_id = ?",
                    (status, timestamp_seconds, first_mention_seconds, evidence, announced_time,
                     json.dumps(cues or []), source_status, now, now if status != "none" else None, video_id))
            else:
                conn.execute(
                    "INSERT INTO transcript_checks (video_id, status, timestamp_seconds, first_mention_seconds, "
                    "evidence, announced_time, cues, source_status, checks, last_checked_at, found_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                    (video_id, status, timestamp_seconds, first_mention_seconds, evidence, announced_time,
                     json.dumps(cues or []), source_status, now, now if status != "none" else None))

    def radio_meta_get(self, key: str) -> str | None:
        with self._tx() as conn:
            row = conn.execute("SELECT value FROM radio_meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def radio_meta_set(self, key: str, value: str) -> None:
        with self._tx() as conn:
            conn.execute("INSERT INTO radio_meta (key, value) VALUES (?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

    def radio_has_tracks(self) -> bool:
        with self._tx() as conn:
            return conn.execute("SELECT 1 FROM radio_tracks LIMIT 1").fetchone() is not None

    def radio_reset(self) -> None:
        """Forget every saved track and the on-air position (used when the Telegram channel changes)."""
        with self._tx() as conn:
            conn.execute("DELETE FROM radio_tracks")
            conn.execute("UPDATE radio_state SET message_id = NULL, started_at = NULL, "
                         "last_synced_id = 0, updated_at = ? WHERE id = 1", (utcnow(),))
