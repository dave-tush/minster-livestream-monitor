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
