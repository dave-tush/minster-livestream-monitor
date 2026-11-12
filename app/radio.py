"""Dominion Radio: a 24/7 radio station built from the audio posts in a Telegram channel.

How it works
------------
* SOURCE   A Telegram *user* session (Telethon / MTProto) reads the channel. The plain Bot API
           cannot read old posts and caps downloads at 20 MB, so it cannot start "from the beginning".
* PLAYLIST Every audio/voice post becomes a track, ordered by message_id = oldest upload first.
           New uploads are picked up by a periodic sync and join the end of the queue.
* CLOCK    The station keeps ONE "on air" pointer (track + position), stored in SQLite.
           A monotonic clock drives progression within a running process, and the position is
           checkpointed to SQLite every few seconds, so server downtime does not advance playback.
           When the last track finishes, the station loops back to the first.
* FILES    Audio is downloaded lazily into a small disk cache (current + next few tracks) and old
           files are evicted, so a big channel does not fill the disk. Downloads resume after an
           interruption and are size-checked before a file is accepted.
* PROGRAMME The administrator can STOP the station, and set a SEASON (a weekly / seasonal emphasis such
           as "Consecration"): between two dates only that season's sermons are broadcast, found by
           keyword in their titles and/or ticked by hand. Outside the dates the whole library plays.
"""
import asyncio
import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from .config import Settings
from .database import Database
from .seasons import build_playlist, clean_keywords

log = logging.getLogger("radio")

TICK_SECONDS = 15           # seconds between Telegram-work iterations; checkpoints persist every ~5 s regardless
DOWNLOAD_CHUNK = 512 * 1024  # Telegram download request size; resume points are multiples of this


# --------------------------------------------------------------------------- source
@dataclass
class TrackMeta:
    message_id: int
    title: str
    duration: int
    performer: str = ""
    file_size: int = 0
    mime_type: str = "audio/mpeg"
    ext: str = ".mp3"
    posted_at: str | None = None

    def as_row(self) -> dict:
        return self.__dict__.copy()


class AudioSource(Protocol):
    """What the engine needs from Telegram. Tests swap in a fake."""
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def fetch_since(self, min_id: int) -> tuple[list[TrackMeta], int]:
        """Audio posts newer than `min_id`, oldest first, plus the highest message id seen."""
    async def download(self, message_id: int, dest: Path) -> None: ...


def _safe_ext(ext: str | None) -> str:
    return ext if ext and re.fullmatch(r"\.[A-Za-z0-9]{1,5}", ext) else ".mp3"


class TelethonSource:
    """Reads a Telegram channel with a user session string (see scripts/telegram_login.py)."""

    def __init__(self, api_id: int, api_hash: str, session_string: str, channel: str):
        self.api_id, self.api_hash = api_id, api_hash
        self.session_string, self.channel = session_string, channel
        self._client = None
        self._entity = None

    async def connect(self) -> None:
        from telethon import TelegramClient            # imported here so the rest of the app
        from telethon.sessions import StringSession    # still runs if telethon isn't installed

        self._client = TelegramClient(StringSession(self.session_string), self.api_id, self.api_hash)
        await self._client.connect()
        if not await self._client.is_user_authorized():
            raise RuntimeError("TELEGRAM_SESSION_STRING is invalid or expired. "
                               "Generate a new one with: python scripts/telegram_login.py")
        target: str | int = self.channel
        if re.fullmatch(r"-?\d+", self.channel):
            target = int(self.channel)
        try:
            self._entity = await self._client.get_entity(target)
        except ValueError:
            await self._client.get_dialogs()            # fills the cache so numeric ids resolve
            self._entity = await self._client.get_entity(target)
        log.info("Connected to Telegram channel: %s", getattr(self._entity, "title", self.channel))

    async def disconnect(self) -> None:
        if self._client:
            await self._client.disconnect()

    async def fetch_since(self, min_id: int) -> tuple[list[TrackMeta], int]:
        tracks: list[TrackMeta] = []
        skipped: list[int] = []
        max_seen = min_id
        async for msg in self._client.iter_messages(self._entity, min_id=min_id, reverse=True):
            max_seen = max(max_seen, msg.id)
            f = msg.file
            if f is None or not (msg.audio or msg.voice or (f.mime_type or "").startswith("audio/")):
                continue
            if not f.duration:
                skipped.append(msg.id)
                continue
            caption = (msg.message or "").strip().splitlines()
            title = (f.title or (caption[0] if caption else "")
                     or (Path(f.name).stem if f.name else "") or f"Track {msg.id}")
            tracks.append(TrackMeta(
                message_id=msg.id, title=title[:200], duration=int(f.duration),
                performer=(f.performer or "")[:200], file_size=f.size or 0,
                mime_type=f.mime_type or "audio/mpeg", ext=_safe_ext(f.ext),
                posted_at=msg.date.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")))
        if skipped:
            log.warning("Radio: %d audio post(s) were left out because Telegram gave them no duration "
                        "(first ones: %s). They are not in the rotation.", len(skipped), skipped[:8])
        return tracks, max_seen

    async def download(self, message_id: int, dest: Path) -> None:
        """Download a post's file to `dest`. If `dest` already holds the start of the file (an
        interrupted earlier attempt) the download carries on from there instead of starting over."""
        msg = await self._client.get_messages(self._entity, ids=message_id)
        if msg is None or msg.file is None:
            raise FileNotFoundError(f"Telegram post {message_id} no longer has a file")
        total = msg.file.size or 0
        offset = 0
        if dest.exists():
            have = dest.stat().st_size
            if not (total and have > total):                   # bigger than the file: start again
                offset = (have // DOWNLOAD_CHUNK) * DOWNLOAD_CHUNK   # keep whole chunks only
                if offset:
                    log.info("Radio: resuming download of post %s at %.1f MB", message_id, offset / 1048576)
            with open(dest, "r+b") as fh:
                fh.truncate(offset)
        with open(dest, "ab" if offset else "wb") as fh:
            async for data in self._client.iter_download(msg.media, offset=offset, request_size=DOWNLOAD_CHUNK,
                                                         file_size=total or None):
                fh.write(data)


# --------------------------------------------------------------------------- engine
def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_epoch(raw: str) -> datetime:
    try:
        return _parse(raw if raw.endswith("Z") else raw + "Z") if "T" in raw else \
            datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime(2026, 1, 1, tzinfo=timezone.utc)


def _as_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _load_tz(name: str) -> tuple[tzinfo, str]:
    try:
        return ZoneInfo(name), name
    except Exception:                                   # unknown name, or no tz database on this machine
        log.warning("Radio: time zone '%s' is not available; season dates will use UTC. "
                    "Set RADIO_TIMEZONE to an IANA name such as Africa/Lagos.", name)
        return timezone.utc, "UTC"


def _public(track: dict) -> dict:
    return {"id": track["message_id"], "title": track["title"], "performer": track["performer"],
            "duration": track["duration"], "audio_url": f"/radio/audio/{track['message_id']}"}


class RadioEngine:
    def __init__(self, settings: Settings, db: Database, source: AudioSource,
                 clock=lambda: datetime.now(timezone.utc),
                 mono_clock=time.monotonic,
                 checkpoint_interval_seconds: float = 5.0):
        self.settings, self.db, self.source = settings, db, source
        self._clock = clock                      # wall clock (UTC) for persisted timestamps
        self._mono_clock = mono_clock            # monotonic clock for playback progression
        self.cache_dir = Path(settings.radio_cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()            # serialises on-air pointer updates
        self._dl_locks: dict[int, asyncio.Lock] = {}
        self._downloading: set[int] = set()
        self._dl_error: dict[int, str] = {}            # last download problem per track (shown to the admin)
        self._dl_fail: dict[int, tuple[int, float]] = {}   # failures so far, and when prefetch may retry
        self._prefetch_task: asyncio.Task | None = None
        self.tz, self.tz_name = _load_tz(settings.radio_timezone)
        self.epoch = _parse_epoch(settings.radio_epoch)
        self.last_error: str | None = None
        self.last_sync_at: str | None = None
        self._mono_at_checkpoint: float | None = None   # mono time when checkpoint was persisted
        self._position_at_checkpoint: float = 0.0       # position_seconds at checkpoint time
        self._checkpoint_interval = max(checkpoint_interval_seconds, 0.5)

    # ---- programming: stop, resume, seasons ---------------------------------
    @staticmethod
    def _season_state(prog: dict, today: date) -> str:
        """off | scheduled (starts later) | active | ended (end date has passed)."""
        if not prog["season_on"]:
            return "off"
        start, end = _as_date(prog["start_date"]), _as_date(prog["end_date"])
        if start and today < start:
            return "scheduled"
        if end and today > end:
            return "ended"
        return "active"

    def _scoped(self, prog: dict, now: datetime) -> bool:
        """True while a season is active AND has sermons: only those are broadcast."""
        return (self._season_state(prog, now.astimezone(self.tz).date()) == "active"
                and self.db.radio_playlist_count() > 0)

    def is_stopped(self) -> bool:
        return self.db.radio_program_get()["stopped"]

    def refresh_playlist(self) -> int:
        """Rebuild the season playlist from its keywords and hand-picked sermons. Returns its size."""
        prog = self.db.radio_program_get()
        ids: list[int] = []
        if prog["season_on"]:
            keywords = prog["keywords"] if prog["use_keywords"] else []
            ids, _ = build_playlist(self.db.radio_tracks_brief(), keywords, prog["picks"])
        self.db.radio_playlist_set(ids)
        return len(ids)

    def preview_season(self, keywords, use_keywords: bool, track_ids) -> dict:
        """What a season would contain (nothing is saved): used by the admin screen as you type."""
        kws = clean_keywords(keywords) if use_keywords else []
        ids, matched = build_playlist(self.db.radio_tracks_brief(), kws, [int(i) for i in track_ids or []])
        return {"playlist": ids, "matched": matched, "total_seconds": self._seconds(ids)}

    def _seconds(self, ids: list[int]) -> int:
        wanted = set(ids)
        return sum(t["duration"] for t in self.db.radio_tracks_brief() if t["message_id"] in wanted)

    def set_season(self, *, name: str, keywords=None, use_keywords: bool = True, start_date: str | None = None,
                   end_date: str | None = None, track_ids=(), go_live: bool = False,
                   now: datetime | None = None) -> dict:
        """Save the season and start using it. Raises ValueError with a message fit for the admin screen."""
        now = now or self._clock()
        name = re.sub(r"\s+", " ", name or "").strip()[:80]
        if not name:
            raise ValueError("Give the season a name, for example Consecration.")
        start, end = _as_date(start_date), _as_date(end_date)
        if (start_date and not start) or (end_date and not end):
            raise ValueError("Dates must look like 2026-11-30.")
        if start and end and end < start:
            raise ValueError("The 'to' date is before the 'from' date.")
        kws = clean_keywords(keywords)
        if use_keywords and not kws:
            kws = [name]
        known = {t["message_id"] for t in self.db.radio_tracks_brief()}
        picks = sorted({int(i) for i in track_ids if int(i) in known})
        if not use_keywords and not picks:
            raise ValueError("Tick at least one sermon, or turn on matching by keyword.")
        playlist, _ = build_playlist(self.db.radio_tracks_brief(), kws if use_keywords else [], picks)
        if not playlist:
            raise ValueError("No sermon title matches those keywords yet. Change the keywords, "
                             "or tick sermons from the list.")
        self.db.radio_program_save_season(name=name, keywords=kws, use_keywords=use_keywords,
                                          start_date=start.isoformat() if start else None,
                                          end_date=end.isoformat() if end else None, picks=picks)
        self.refresh_playlist()
        if go_live:
            self.resume(now)
        log.info("Radio: season '%s' saved (%d sermons, %s to %s).", name, len(playlist),
                 start or "now", end or "until ended")
        return self.admin_overview(now)

    def end_season(self, now: datetime | None = None) -> dict:
        """Back to the whole library. The season's settings are kept so it can be switched on again."""
        self.db.radio_program_set_season_on(False)
        self.refresh_playlist()
        log.info("Radio: season ended by the administrator.")
        return self.admin_overview(now)

    def stop(self, now: datetime | None = None) -> dict:
        """Take the station off air. The on-air position is frozen, so resume() carries on from it."""
        now = now or self._clock()
        prog = self.db.radio_program_get()
        if not prog["stopped"]:
            with self._lock:
                self._advance(now, self._scoped(prog, now))      # save exactly where we are
                self.db.radio_program_set_stopped(True, _fmt(now))
            log.info("Radio: stopped by the administrator.")
        return self.admin_overview(now)

    def resume(self, now: datetime | None = None) -> dict:
        """Back on air from the moment it was stopped (the time off air is not counted)."""
        now = now or self._clock()
        prog = self.db.radio_program_get()
        if prog["stopped"]:
            with self._lock:
                state = self.db.radio_get_state()
                if prog["stopped_at"] and state["started_at"] and state["message_id"]:
                    gap = max(0.0, (now - _parse(prog["stopped_at"])).total_seconds())
                    self.db.radio_set_state(state["message_id"],
                                            _fmt(_parse(state["started_at"]) + timedelta(seconds=gap)))
                self.db.radio_program_set_stopped(False)
            log.info("Radio: back on air.")
        return self.admin_overview(now)

    def program_status(self, now: datetime | None = None) -> dict:
        now = now or self._clock()
        prog = self.db.radio_program_get()
        today = now.astimezone(self.tz).date()
        state = self._season_state(prog, today)
        size = self.db.radio_playlist_count() if prog["season_on"] else 0
        on_season = state == "active" and size > 0
        return {
            "stopped": prog["stopped"],
            "mode": "stopped" if prog["stopped"] else ("season" if on_season else "library"),
            "timezone": self.tz_name,
            "today": today.isoformat(),
            "library_tracks": self.db.radio_count(),
            "season": {
                "enabled": prog["season_on"], "state": state, "name": prog["season_name"],
                "keywords": prog["keywords"], "use_keywords": prog["use_keywords"],
                "start_date": prog["start_date"], "end_date": prog["end_date"],
                "picks": prog["picks"], "tracks": size,
            },
        }

    def _track_health(self, public: dict | None) -> dict | None:
        """Is this track on disk yet? (the answer to "why does the next sermon not play?")"""
        if not public:
            return None
        track = self.db.radio_get_track(public["id"])
        if track is None:
            return None
        path = self.cache_path(track)
        part = path.with_name(path.name + ".part")
        size = track["file_size"] or 0
        if path.exists():
            have, ready = size or path.stat().st_size, True
        else:
            have, ready = (part.stat().st_size if part.exists() else 0), False
        return {"id": track["message_id"], "title": track["title"], "ready": ready,
                "downloading": track["message_id"] in self._downloading,
                "percent": 100 if ready else (min(99, int(100 * have / size)) if size else 0),
                "error": None if ready else self._dl_error.get(track["message_id"])}

    def admin_overview(self, now: datetime | None = None) -> dict:
        now = now or self._clock()
        out = self.program_status(now)
        playing = self.now_playing(now)
        out["on_air"] = playing and {"id": playing["track"]["id"], "title": playing["track"]["title"],
                                     "started_at": playing["started_at"]}
        out["health"] = {"current": self._track_health(playing and playing["track"]),
                         "next": self._track_health(playing and playing["next"]),
                         "last_error": self.last_error, "last_sync_at": self.last_sync_at}
        return out

    # ---- the on-air clock ------------------------------------------------
    def now_playing(self, now: datetime | None = None) -> dict | None:
        """What is on air right now (advances the pointer if the track has finished).
        None when the station is stopped or has nothing to play."""
        now = now or self._clock()
        prog = self.db.radio_program_get()
        if prog["stopped"]:
            return None
        scoped = self._scoped(prog, now)
        with self._lock:
            track, started, position = self._advance_locked(now, scoped)
            if track is None:
                return None
            nxt = self.db.radio_next_track(track["message_id"], scoped)
            return {
                "station": self.settings.radio_name,
                "track": _public(track),
                "position": round(position, 2),
                "remaining": round(max(0.0, track["duration"] - position), 2),
                "started_at": _fmt(started),
                "next": _public(nxt) if nxt and nxt["message_id"] != track["message_id"] else None,
                "server_time": _fmt(now),
                "tracks_in_rotation": self.db.radio_count(scoped),
                "season": ({"name": prog["season_name"],
                            "emphasis": self.db.radio_settings_get().get("emphasis", ""),
                            "start_date": prog["start_date"], "end_date": prog["end_date"]} if scoped else None),
            }

    def _current_position_locked(self, state: dict, started: datetime,
                                 now: datetime) -> tuple[float, datetime]:
        """Compute playback position in seconds and the current track's start time.

        Must hold self._lock. Prefers the monotonic anchor when set, then the
        persisted checkpoint from SQLite (server downtime excluded). The remaining
        branch handles unanchored states with no checkpoint data; callers route
        legacy records through the deterministic bootstrap instead, so it is only
        kept as defence in depth.
        """
        if self._mono_at_checkpoint is not None:
            elapsed_mono = self._mono_clock() - self._mono_at_checkpoint
            return self._position_at_checkpoint + elapsed_mono, started
        checkpoint_at = state.get("checkpoint_at")
        position_seconds = state.get("position_seconds")
        if checkpoint_at is not None and position_seconds is not None:
            # A checkpoint from a previous process exists: resume from it and anchor the
            # monotonic clock to this process. Server downtime is not played back.
            position = float(position_seconds)
        else:
            # No checkpoint data (fresh install or a pre-checkpoint legacy row): fall back
            # to the wall clock, skipping whole playlist loops.
            position = (now - started).total_seconds()
            total = self.db.radio_total_seconds()
            if total > 0 and position >= total:
                loops = int(position // total)
                position -= loops * total
                started = started + timedelta(seconds=loops * total)
        self._position_at_checkpoint = position
        self._mono_at_checkpoint = self._mono_clock()
        return position, started

    def _advance_locked(self, now: datetime, scoped: bool = False) -> tuple[dict | None, datetime, float]:
        """Advance the on-air pointer using persisted checkpoints and the active playlist."""
        state = self.db.radio_get_state()
        track = self.db.radio_get_track(state["message_id"]) if state["message_id"] else None
        if (track is None or not track["available"] or not state.get("started_at")
                or (scoped and not self.db.radio_in_scope(track["message_id"]))):
            if state.get("message_id") is None:
                track, started = self._join_schedule(now, scoped)
                if track is None:
                    return None, now, 0.0
            else:
                track = self.db.radio_first_track(scoped)
                if track is None:
                    return None, now, 0.0
                started = now
            position = 0.0
            self._mono_at_checkpoint = self._mono_clock()
            self._position_at_checkpoint = 0.0
        else:
            started = _parse(state["started_at"])
            if started > now:
                started = now
            checkpoint_at = state.get("checkpoint_at")
            position_seconds = state.get("position_seconds")
            if checkpoint_at is not None and position_seconds is not None:
                position, started = self._current_position_locked(state, started, now)
            else:
                started = now
                position = 0.0
                self._position_at_checkpoint = position
                self._mono_at_checkpoint = self._mono_clock()

            safety = 0
            limit = self.db.radio_count(scoped) + 2
            while position >= track["duration"] and safety < limit:
                position -= track["duration"]
                nxt = self.db.radio_next_track(track["message_id"], scoped)
                if nxt is None:
                    nxt = self.db.radio_first_track(scoped)
                    if nxt is None:
                        return None, now, 0.0
                started += timedelta(seconds=track["duration"])
                track = nxt
                safety += 1

        if (track["message_id"] != state["message_id"] or _fmt(started) != state["started_at"]):
            self.db.radio_set_state(track["message_id"], _fmt(started),
                                    position_seconds=position, checkpoint_at=_fmt(now))
            self._mono_at_checkpoint = self._mono_clock()
            self._position_at_checkpoint = float(position)
        return track, started, position

        # Persist if track or started_at changed
        if (track["message_id"] != state["message_id"]
                or _fmt(started) != state["started_at"]):
            self.db.radio_set_state(
                track["message_id"], _fmt(started),
                position_seconds=position,
                checkpoint_at=_fmt(now))
            self._mono_at_checkpoint = self._mono_clock()
            self._position_at_checkpoint = float(position)

        return track, started, position

    def _checkpoint_locked(self, now: datetime) -> None:
        """Persist current playback position. Must hold self._lock.

        Reads current track and position (computed from the monotonic clock),
        then writes them to radio_state atomically.
        """
        state = self.db.radio_get_state()
        track = self.db.radio_get_track(state["message_id"]) if state["message_id"] else None

        if (track is None or not track["available"]
                or False):
            # A legacy record without checkpoint data gets bootstrapped by
            # _advance_locked, which restarts the track at position 0 and sets
            # checkpoint_at. Without that row nothing can be checkpointed yet.
            if state.get("message_id") and state.get("started_at"):
                self._advance_locked(now)
            return

        started = _parse(state["started_at"]) if state["started_at"] else now
        if started > now:
            started = now

        # A legacy record (no checkpoint_at) reaches this point with an anchor
        # already established by _init_checkpoint_state or by a prior playback
        # call. Bootstrap it deterministically rather than letting the old
        # wall-clock calculation count downtime as playback.
        if state.get("checkpoint_at") is None:
            self._advance_locked(now)
            return

        # Compute current position from the monotonic checkpoint
        position, started = self._current_position_locked(state, started, now)

        # If position has advanced past current track, advance to next track
        if position >= track["duration"]:
            new_track, new_started, new_position = self._advance_locked(now)
            if new_track is None:
                return
            track = new_track
            started = new_started
            position = new_position

        # Persist current state
        self.db.radio_set_state(
            track["message_id"], _fmt(started),
            position_seconds=position,
            checkpoint_at=_fmt(now))
        self._mono_at_checkpoint = self._mono_clock()
        self._position_at_checkpoint = float(position)

    def _checkpoint(self, now: datetime | None = None) -> None:
        """Public wrapper: acquires lock, calls _checkpoint_locked."""
        now = now or self._clock()
        with self._lock:
            self._checkpoint_locked(now)

    def _join_schedule(self, now: datetime, scoped: bool) -> tuple[dict | None, datetime]:
        """Where a 24/7 station that began at `self.epoch` and loops the playlist would be right now."""
        schedule = self.db.radio_schedule(scoped)
        total = sum(t["duration"] for t in schedule)
        if not schedule or total <= 0:
            return None, now
        # Preserve the original first-track behavior when no explicit broadcast epoch was configured.
        if self.settings.radio_epoch == "2026-01-01T00:00:00Z":
            track = self.db.radio_first_track(scoped)
            return (track, now) if track else (None, now)
        offset = max(0.0, (now - self.epoch).total_seconds()) % total
        passed = 0
        for t in schedule:
            if offset < passed + t["duration"]:
                track = self.db.radio_get_track(t["message_id"])
                log.info("Radio: no saved position, joining the schedule at '%s' (%d min in).",
                         track["title"], int((offset - passed) // 60))
                return track, now - timedelta(seconds=offset - passed)
            passed += t["duration"]
        return self.db.radio_first_track(scoped), now

    def skip(self, now: datetime | None = None) -> dict:
        """Move straight on to the next sermon (also a quick way to test that sermon changes work)."""
        now = now or self._clock()
        prog = self.db.radio_program_get()
        if prog["stopped"]:
            raise ValueError("The radio is stopped. Put it back on air first.")
        scoped = self._scoped(prog, now)
        with self._lock:
            track, _ = self._advance(now, scoped)
            if track is None:
                raise ValueError("Nothing is on air yet, so there is nothing to skip.")
            nxt = self.db.radio_next_track(track["message_id"], scoped) or track
            self.db.radio_set_state(nxt["message_id"], _fmt(now))
        log.info("Radio: skipped to '%s' by the administrator.", nxt["title"])
        return self.admin_overview(now)

    # ---- syncing the channel --------------------------------------------
    @staticmethod
    def _channel_key(raw: str) -> str:
        """Normalise '@Name', 'https://t.me/Name' and 'name' to the same key."""
        key = raw.strip().lower()
        key = re.sub(r"^https?://(?:www\.)?(?:t|telegram)\.me/", "", key)
        return key.split("?")[0].strip("/").lstrip("@")

    def _ensure_channel(self) -> None:
        """If TELEGRAM_CHANNEL changed since the tracks were saved, start over.

        Post numbers are per channel, so tracks, the sync position and cached files from another
        channel would otherwise be mixed in (and the new channel's early posts skipped).

        If the channel marker is missing but tracks already exist, the tracks are adopted under
        the currently configured channel instead of being destructively reset: their origin
        predates channel tracking and cannot be verified, so resetting would be unsafe. An
        actual channel change (marker present and different) still resets the playlist.
        """
        key = self._channel_key(self.settings.telegram_channel)
        if not key:
            return
        saved = self.db.radio_meta_get("channel")
        if saved == key:
            return
        if saved is None:
            # No channel marker has ever been recorded. Adopt the existing tracks (if any)
            # under the currently configured channel instead of resetting them: we cannot
            # establish their origin, so destructive reset would be unsafe.
            if self.db.radio_has_tracks():
                log.warning("Radio: channel marker missing; adopting existing tracks under '%s'. "
                            "Tracks predate channel tracking; ownership cannot be verified.", key)
        else:
            # An actual channel change: the previously tracked channel differs from the current one.
            log.warning("Radio: Telegram channel is now '%s' (was '%s'). Clearing the old playlist and "
                        "starting this channel from its first upload.", key, saved)
            self.db.radio_reset()
            for f in self.cache_dir.iterdir():
                if f.is_file():
                    f.unlink(missing_ok=True)
        self.db.radio_meta_set("channel", key)

    async def sync_once(self) -> int:
        """Pull new audio posts from Telegram. Returns how many tracks were added."""
        self._ensure_channel()
        last = self.db.radio_get_state()["last_synced_id"]
        tracks, max_seen = await self.source.fetch_since(last)
        added = self.db.radio_add_tracks([t.as_row() for t in tracks], max_seen)
        self.last_sync_at = _fmt(self._clock())
        if added:
            log.info("Radio: added %d new track(s); %d in rotation.", added, self.db.radio_count())
            self.refresh_playlist()                        # a new upload may belong to the season
        return added

    # ---- file cache ------------------------------------------------------
    def cache_path(self, track: dict) -> Path:
        return self.cache_dir / f"{track['message_id']}{track['ext']}"

    def _note_failure(self, message_id: int, problem) -> None:
        """Remember a failed download; prefetch waits longer before each retry (30 s, 60 s ... 15 min)."""
        count = self._dl_fail.get(message_id, (0, 0.0))[0] + 1
        self._dl_fail[message_id] = (count, time.monotonic() + min(30 * 2 ** (count - 1), 900))
        self._dl_error[message_id] = str(problem)[:200] or type(problem).__name__
        log.error("Radio: download of %s failed (attempt %d): %s", message_id, count, problem)

    def _backing_off(self, message_id: int) -> bool:
        return time.monotonic() < self._dl_fail.get(message_id, (0, 0.0))[1]

    @staticmethod
    def _incomplete(part: Path, track: dict) -> str | None:
        """None if the downloaded file is the size Telegram promised; otherwise what is wrong."""
        if not part.exists() or part.stat().st_size == 0:
            return "the download produced no data"
        expected, size = track["file_size"] or 0, part.stat().st_size
        if expected and size != expected:
            if size > expected:
                part.unlink(missing_ok=True)              # cannot be right: start over next time
            return f"incomplete download ({size} of {expected} bytes)"
        return None

    async def ensure_cached(self, message_id: int) -> Path | None:
        """Local file for a track, downloading it first if needed. None if it can't be fetched.
        A partly downloaded file is kept and continued on the next attempt."""
        track = self.db.radio_get_track(message_id)
        if track is None:
            return None
        path = self.cache_path(track)
        if path.exists():
            return path
        lock = self._dl_locks.setdefault(message_id, asyncio.Lock())
        async with lock:
            if path.exists():
                return path
            part = path.with_name(path.name + ".part")
            self._downloading.add(message_id)
            try:
                log.info("Radio: downloading '%s' (%s)...", track["title"], message_id)
                try:
                    await self.source.download(message_id, part)
                except FileNotFoundError:
                    part.unlink(missing_ok=True)
                    self.db.radio_set_available(message_id, False)   # post was deleted
                    log.error("Radio: post %s was deleted from the channel; skipping it.", message_id)
                    return None
                except Exception as exc:
                    self._note_failure(message_id, exc)
                    return None
                problem = self._incomplete(part, track)
                if problem:
                    self._note_failure(message_id, problem)
                    return None
                part.replace(path)
                self._dl_fail.pop(message_id, None)
                self._dl_error.pop(message_id, None)
            finally:
                self._downloading.discard(message_id)
        return path

    async def prefetch(self) -> None:
        """Make sure the on-air track and the next few are on disk (never raises)."""
        try:
            current = await asyncio.to_thread(self.now_playing)
            if current is None:
                return                                   # stopped, or nothing to play
            scoped = current["season"] is not None
            ids = [current["track"]["id"]]
            nxt = current["next"]
            for _ in range(self.settings.radio_prefetch_count):
                if not nxt:
                    break
                ids.append(nxt["id"])
                after = self.db.radio_next_track(nxt["id"], scoped)
                nxt = _public(after) if after and after["message_id"] not in ids else None
            for mid in ids:
                if not self._backing_off(mid):
                    await self.ensure_cached(mid)
            self.evict(keep=set(ids))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("Radio: prefetch error: %s", exc)

    def _kick_prefetch(self) -> None:
        """Run prefetch in the background so a slow download never holds up syncing or reconnecting."""
        if self._prefetch_task is None or self._prefetch_task.done():
            self._prefetch_task = asyncio.create_task(self.prefetch())

    def evict(self, keep: set[int]) -> None:
        """Delete the oldest cached files once the cache is over its size limit."""
        limit = self.settings.radio_cache_max_mb * 1024 * 1024
        files = [p for p in self.cache_dir.iterdir() if p.is_file() and not p.name.endswith(".part")]
        total = sum(p.stat().st_size for p in files)
        for p in sorted(files, key=lambda p: p.stat().st_mtime):
            if total <= limit:
                break
            if p.stem.isdigit() and int(p.stem) in keep:
                continue
            total -= p.stat().st_size
            p.unlink(missing_ok=True)

    # ---- background loop -------------------------------------------------
    async def run_forever(self, stop: asyncio.Event) -> None:
        """Connect, sync, keep the next tracks downloaded. Never raises (except cancellation)."""
        log.info("Radio started: %s", self.settings.radio_name)
        saved = self.db.radio_count()
        if saved:
            log.info("Radio: found %d saved tracks and the saved on-air position; only new uploads will be scanned.", saved)
        else:
            log.warning("Radio: no saved tracks found (first run, or the disk was wiped by a redeploy/restart). "
                        "Scanning the whole channel now; the station is off air until that finishes. "
                        "Put the database and cache on a persistent disk to avoid this.")
        connected = False
        next_sync = 0.0
        next_checkpoint = 0.0
        next_attempt = 0.0          # network work is allowed at/after this loop time
        loop = asyncio.get_running_loop()
        failures = 0
        # Initialize checkpoint state from persisted database on startup.
        try:
            await asyncio.to_thread(self._init_checkpoint_state)
        except Exception as exc:
            log.error("Radio: failed to initialize checkpoint state: %s", exc)

        try:
            while not stop.is_set():
                # Periodic checkpoint: runs even with no listeners and even while
                # Telegram is unreachable.
                if loop.time() >= next_checkpoint:
                    try:
                        await asyncio.to_thread(self._checkpoint)
                    except Exception as exc:
                        log.error("Radio: periodic checkpoint failed: %s", exc)
                    next_checkpoint = loop.time() + self._checkpoint_interval

                if loop.time() >= next_attempt:
                    try:
                        if not connected:
                            await self.source.connect()
                            connected = True
                        if loop.time() >= next_sync:
                            await self.sync_once()
                            next_sync = loop.time() + self.settings.radio_sync_interval_seconds
                        await self.prefetch()
                        self.last_error, failures = None, 0
                        next_attempt = loop.time() + TICK_SECONDS
                    except Exception as exc:
                        failures += 1
                        self.last_error = str(exc)
                        delay = min(TICK_SECONDS * (2 ** failures), 600)
                        log.error("Radio error: %s. Retrying in %ds.", exc, delay)
                        next_attempt = loop.time() + delay
                        if connected:                       # reconnect from scratch after a failure
                            try:
                                await self.source.disconnect()
                            except Exception:
                                pass
                            connected = False

                # Wake when the next checkpoint (or the next network attempt) is due,
                # whichever comes first — about every checkpoint_interval seconds.
                wait = min(TICK_SECONDS,
                           max(0.25, next_checkpoint - loop.time()),
                           max(0.25, next_attempt - loop.time()))
                try:
                    await asyncio.wait_for(stop.wait(), timeout=wait)
                except asyncio.TimeoutError:
                    pass
        finally:
            # Graceful shutdown: best-effort final checkpoint. Runs even on cancellation,
            # and before Telegram disconnect; checkpoint failure never masks the shutdown.
            try:
                self._checkpoint()
            except Exception as exc:
                log.error("Radio: final checkpoint failed: %s", exc)
            try:
                await self.source.disconnect()
            except Exception as exc:
                log.error("Radio: disconnect failed during shutdown: %s", exc)
            log.info("Radio stopped.")

    def _init_checkpoint_state(self) -> None:
        """Load persisted checkpoint state from the database on startup.

        Sets _mono_at_checkpoint and _position_at_checkpoint from the last saved checkpoint
        so playback resumes from it without advancing through server downtime. Rows
        without checkpoint data (fresh install, or pre-checkpoint legacy rows) are left
        alone here; they are bootstrapped deterministically on the first playback call,
        restarting the track at position 0 with checkpoint_at set to that moment.
        """
        with self._lock:
            state = self.db.radio_get_state()
            if not state or not state["message_id"]:
                return
            track = self.db.radio_get_track(state["message_id"])
            if (track is None or not track["available"]
                    or False):
                return
            position_seconds = state.get("position_seconds")
            checkpoint_at = state.get("checkpoint_at")
            if checkpoint_at is None or position_seconds is None:
                return
            self._position_at_checkpoint = float(position_seconds)
            self._mono_at_checkpoint = self._mono_clock()
