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
           files are evicted, so a big channel does not fill the disk.
"""
import asyncio
import inspect
import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

from .config import Settings
from .database import Database

log = logging.getLogger("radio")

TICK_SECONDS = 15           # seconds between Telegram-work iterations; checkpoints persist every ~5 s regardless
SYNC_BATCH = 400            # channel posts read per batch, so tracks become playable within seconds of a restart
ENDED_GRACE_SECONDS = 20    # a listener's "this file ended" report is honoured this close to the station's own end
SNAPSHOT_MARK = "DOMINION_RADIO_STATE"
GUESS_BYTES_PER_SECOND = 16000   # 128 kbps, only used for audio posts that carry no duration at all


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
    async def fetch_since(self, min_id: int, limit: int | None = None) -> tuple[list[TrackMeta], int]:
        """Audio posts newer than `min_id`, oldest first (at most `limit` posts are read), plus the
        highest message id seen."""
    async def download(self, message_id: int, dest: Path) -> None: ...


def _safe_ext(ext: str | None) -> str:
    return ext if ext and re.fullmatch(r"\.[A-Za-z0-9]{1,5}", ext) else ".mp3"


def probe_duration(path: Path) -> int | None:
    """Real length of an audio file in whole seconds (None if it cannot be read)."""
    try:
        import mutagen
        f = mutagen.File(str(path))
        length = getattr(getattr(f, "info", None), "length", None)
        return int(length) if length and length >= 1 else None
    except Exception:
        return None


class TelethonSource:
    """Reads a Telegram channel with a user session string (see scripts/telegram_login.py)."""

    def __init__(self, api_id: int, api_hash: str, session_string: str, channel: str,
                 include_no_duration: bool = True):
        self.api_id, self.api_hash = api_id, api_hash
        self.session_string, self.channel = session_string, channel
        self.include_no_duration = include_no_duration
        self._client = None
        self._entity = None
        self._snapshot_msg_id: int | None = None

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

    def _to_track(self, msg) -> TrackMeta | None:
        f = msg.file
        if f is None or not (msg.audio or msg.voice or (f.mime_type or "").startswith("audio/")):
            return None
        duration = int(f.duration or 0)
        if not duration:
            # Audio sent as a plain file often has no duration. Estimate it from the size so the
            # sermon still plays; the real length is read from the file once it is downloaded.
            if not (self.include_no_duration and f.size):
                return None
            duration = max(1, int(f.size / GUESS_BYTES_PER_SECOND))
        caption = (msg.message or "").strip().splitlines()
        title = (f.title or (caption[0] if caption else "")
                 or (Path(f.name).stem if f.name else "") or f"Track {msg.id}")
        return TrackMeta(
            message_id=msg.id, title=title[:200], duration=duration,
            performer=(f.performer or "")[:200], file_size=f.size or 0,
            mime_type=f.mime_type or "audio/mpeg", ext=_safe_ext(f.ext),
            posted_at=msg.date.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

    async def fetch_since(self, min_id: int, limit: int | None = None) -> tuple[list[TrackMeta], int]:
        tracks: list[TrackMeta] = []
        max_seen = min_id
        async for msg in self._client.iter_messages(self._entity, min_id=min_id, reverse=True, limit=limit):
            max_seen = max(max_seen, msg.id)
            t = self._to_track(msg)
            if t is not None:
                tracks.append(t)
        return tracks, max_seen

    async def fetch_track(self, message_id: int) -> TrackMeta | None:
        msg = await self._client.get_messages(self._entity, ids=message_id)
        return self._to_track(msg) if msg is not None else None

    # The on-air position is kept in one message in the account's own "Saved Messages", so it
    # survives Render wiping the disk. Only this one message is ever read, edited or created.
    async def load_snapshot(self) -> str | None:
        async for m in self._client.iter_messages("me", search=SNAPSHOT_MARK, limit=10):
            if (m.message or "").startswith(SNAPSHOT_MARK):
                self._snapshot_msg_id = m.id
                return m.message
        return None

    async def save_snapshot(self, text: str) -> None:
        if self._snapshot_msg_id is None:
            await self.load_snapshot()
        if self._snapshot_msg_id is not None:
            try:
                await self._client.edit_message("me", self._snapshot_msg_id, text)
                return
            except Exception as exc:
                if "not modified" in str(exc).lower():
                    return
                self._snapshot_msg_id = None            # message deleted? make a new one
        sent = await self._client.send_message("me", text)
        self._snapshot_msg_id = sent.id

    async def download(self, message_id: int, dest: Path) -> None:
        msg = await self._client.get_messages(self._entity, ids=message_id)
        if msg is None or msg.file is None:
            raise FileNotFoundError(f"Telegram post {message_id} no longer has a file")
        await self._client.download_media(msg, file=str(dest))


# --------------------------------------------------------------------------- engine
def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _public(track: dict) -> dict:
    return {"id": track["message_id"], "title": track["title"], "performer": track["performer"],
            "duration": track["duration"], "audio_url": f"/radio/audio/{track['message_id']}"}


class RadioEngine:
    def __init__(self, settings: Settings, db: Database, source: AudioSource,
                 clock=lambda: datetime.now(timezone.utc),
                 mono_clock=time.monotonic,
                 checkpoint_interval_seconds: float = 5.0,
                 state_store=None):
        self.settings, self.db, self.source = settings, db, source
        self._clock = clock                      # wall clock (UTC) for persisted timestamps
        self._mono_clock = mono_clock            # monotonic clock for playback progression
        self.cache_dir = Path(settings.radio_cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()            # serialises on-air pointer updates
        self._dl_locks: dict[int, asyncio.Lock] = {}
        self.last_error: str | None = None
        self.last_sync_at: str | None = None
        self._mono_at_checkpoint: float | None = None   # mono time when checkpoint was persisted
        self._position_at_checkpoint: float = 0.0       # position_seconds at checkpoint time
        self._checkpoint_interval = max(checkpoint_interval_seconds, 0.5)
        self.last_tick_at: float = time.monotonic()      # watchdog: when the background loop last made progress
        self.state_store = state_store                   # optional D1 store (app/d1.py)
        self.last_snapshot_at: str | None = None
        self.restored_from_snapshot = False

    # ---- the on-air clock ------------------------------------------------
    def now_playing(self, now: datetime | None = None, ended_id: int | None = None) -> dict | None:
        """What is on air right now (advances the pointer if the track has finished).

        `ended_id`: a listener's player reports that the file it was playing has finished. If that
        is the on-air track and the station is within ENDED_GRACE_SECONDS of its end, the station
        moves on at once. This covers a Telegram-reported length that is a little longer than the
        real audio, which would otherwise leave listeners in silence until the clock caught up.
        """
        now = now or self._clock()
        with self._lock:
            track, started, position = self._advance_locked(now)
            if track is None:
                return None
            if (ended_id is not None and ended_id == track["message_id"]
                    and track["duration"] - position <= ENDED_GRACE_SECONDS):
                self._position_at_checkpoint = float(track["duration"])
                self._mono_at_checkpoint = self._mono_clock()
                track, started, position = self._advance_locked(now)
                if track is None:
                    return None
            nxt = self.db.radio_next_track(track["message_id"])
            return {
                "station": self.settings.radio_name,
                "track": _public(track),
                "position": round(position, 2),
                "remaining": round(max(0.0, track["duration"] - position), 2),
                "started_at": _fmt(started),
                "next": _public(nxt) if nxt and nxt["message_id"] != track["message_id"] else None,
                "server_time": _fmt(now),
                "tracks_in_rotation": self.db.radio_count(),
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

    def _advance_locked(self, now: datetime) -> tuple[dict | None, datetime, float]:
        """Advance the on-air pointer. Must hold self._lock.

        Returns (track, started_at, position_seconds). position_seconds is computed from the
        persisted checkpoint using the monotonic clock, so server downtime is excluded.
        """
        state = self.db.radio_get_state()
        track = self.db.radio_get_track(state["message_id"]) if state["message_id"] else None

        if track is None or not track["available"]:
            track = self.db.radio_first_track()      # first run, or the on-air track was removed
            if track is None:
                return None, now, 0.0
            started = now
            position = 0.0
            self._mono_at_checkpoint = self._mono_clock()
            self._position_at_checkpoint = 0.0
        else:
            started = _parse(state["started_at"]) if state["started_at"] else now
            if started > now:                            # clock moved backwards: restart this track
                started = now

            checkpoint_at = state.get("checkpoint_at")
            position_seconds = state.get("position_seconds")
            if checkpoint_at is not None and position_seconds is not None:
                # Normal path: resume from the persisted checkpoint.
                position, started = self._current_position_locked(state, started, now)
            else:
                # Legacy record without checkpoint data (position_seconds = 0,
                # checkpoint_at = NULL from the migration default). Bootstrap
                # deterministically: restart this track from position 0 with
                # started_at = checkpoint_at = now. The true pre-upgrade playback
                # position cannot be recovered; restarting from the beginning is
                # the only deterministic option, and after this call the row
                # carries a checkpoint so subsequent calls resume normally and
                # server downtime is excluded from playback progression.
                started = now
                position = 0.0
                self._position_at_checkpoint = position
                self._mono_at_checkpoint = self._mono_clock()

            # Advance past completed tracks
            safety = 0
            limit = self.db.radio_count() + 2
            while position >= track["duration"] and safety < limit:
                position -= track["duration"]
                nxt = self.db.radio_next_track(track["message_id"])
                if nxt is None:
                    nxt = self.db.radio_first_track()
                    if nxt is None:
                        return None, now, 0.0
                    started = started + timedelta(seconds=track["duration"])
                    track = nxt
                    break
                started = started + timedelta(seconds=track["duration"])
                track = nxt
                safety += 1

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

        if track is None or not track["available"]:
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
        """Pull new audio posts from Telegram. Returns how many tracks were added.

        The channel is read in batches and each batch is saved straight away, so after a restart
        the first sermons are playable within seconds instead of after the whole scan.
        """
        self._ensure_channel()
        try:
            batched = "limit" in inspect.signature(self.source.fetch_since).parameters
        except (TypeError, ValueError):
            batched = False
        total = 0
        for _ in range(500):
            last = self.db.radio_get_state()["last_synced_id"]
            tracks, max_seen = (await self.source.fetch_since(last, limit=SYNC_BATCH) if batched
                                else await self.source.fetch_since(last))
            total += self.db.radio_add_tracks([t.as_row() for t in tracks], max_seen)
            self.last_tick_at = time.monotonic()
            if not batched or max_seen <= last:
                break
        self.last_sync_at = _fmt(self._clock())
        if total:
            log.info("Radio: added %d new track(s); %d in rotation.", total, self.db.radio_count())
        return total

    # ---- surviving restarts ----------------------------------------------
    def _snapshot_data(self) -> dict | None:
        cur = self.now_playing()
        if cur is None:
            return None
        return {"channel": self._channel_key(self.settings.telegram_channel),
                "message_id": cur["track"]["id"], "position": cur["position"],
                "saved_at": _fmt(self._clock())}

    async def save_snapshot(self) -> None:
        """Save the on-air position to D1 (main) and Telegram Saved Messages (fallback)."""
        tg_save = getattr(self.source, "save_snapshot", None) if self.settings.radio_snapshot else None
        if not (self.state_store or tg_save):
            return
        data = await asyncio.to_thread(self._snapshot_data)
        if not data:
            return
        saved = False
        if self.state_store:
            try:
                await self.state_store.save(data)
                saved = True
            except Exception as exc:
                log.warning("Radio: could not save the position to D1: %s", exc)
        if tg_save:
            text = (f"{SNAPSHOT_MARK} {json.dumps(data)}\n"
                    "Dominion Radio keeps this message up to date so it can resume after a restart. "
                    "Please do not delete it.")
            try:
                await tg_save(text)
                saved = True
            except Exception as exc:
                log.warning("Radio: could not save the position to Telegram: %s", exc)
        if saved:
            self.last_snapshot_at = _fmt(self._clock())

    async def _load_saved(self) -> dict | None:
        if self.state_store:
            try:
                data = await self.state_store.load()
                if data:
                    return data
            except Exception as exc:
                log.warning("Radio: could not read the saved position from D1: %s", exc)
        load = getattr(self.source, "load_snapshot", None)
        if self.settings.radio_snapshot and load:
            text = await load()
            if text:
                try:
                    return json.loads(text.splitlines()[0][len(SNAPSHOT_MARK):].strip())
                except (ValueError, IndexError):
                    log.warning("Radio: the position saved in Telegram is unreadable.")
        return None

    async def restore_from_snapshot(self) -> bool:
        """After a restart with an empty database: put the station back on the track it was playing."""
        fetch_track = getattr(self.source, "fetch_track", None)
        if fetch_track is None:
            return False
        state = self.db.radio_get_state()
        if state.get("message_id") and state.get("checkpoint_at"):
            return False                                  # the local database already knows
        data = await self._load_saved()
        if not data:
            return False
        try:
            mid, pos = int(data["message_id"]), max(0.0, float(data["position"]))
        except (ValueError, KeyError, TypeError):
            log.warning("Radio: the saved position is unreadable; starting from the first track.")
            return False
        if data.get("channel") != self._channel_key(self.settings.telegram_channel):
            return False                                  # saved for a different channel
        meta = await fetch_track(mid)
        if meta is None:
            return False
        self._ensure_channel()
        self.db.radio_add_tracks([meta.as_row()], 0)
        now = self._clock()
        pos = min(pos, max(0.0, meta.duration - 1))
        self.db.radio_set_state(mid, _fmt(now - timedelta(seconds=pos)), position_seconds=pos,
                                checkpoint_at=_fmt(now))
        await asyncio.to_thread(self._init_checkpoint_state)
        self.restored_from_snapshot = True
        log.info("Radio: resumed '%s' at %d:%02d after a restart.", meta.title, pos // 60, pos % 60)
        return True

    # ---- file cache ------------------------------------------------------
    def cache_path(self, track: dict) -> Path:
        return self.cache_dir / f"{track['message_id']}{track['ext']}"

    async def ensure_cached(self, message_id: int) -> Path | None:
        """Local file for a track, downloading it first if needed. None if it can't be fetched."""
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
            try:
                log.info("Radio: downloading '%s' (%s)...", track["title"], message_id)
                await self.source.download(message_id, part)
                part.replace(path)
                await self._use_real_duration(track, path)
            except Exception as exc:
                part.unlink(missing_ok=True)
                log.error("Radio: download of %s failed: %s", message_id, exc)
                if isinstance(exc, FileNotFoundError):
                    self.db.radio_set_available(message_id, False)   # post was deleted
                return None
        return path

    async def _use_real_duration(self, track: dict, path: Path) -> None:
        """The station clock must match the real audio, or listeners hit silence or a cut-off ending."""
        real = await asyncio.to_thread(probe_duration, path)
        if real and abs(real - track["duration"]) > 2:
            log.info("Radio: '%s' is %ds long, not the %ds Telegram reported; using the real length.",
                     track["title"], real, track["duration"])
            self.db.radio_set_duration(track["message_id"], real)

    async def prefetch(self) -> None:
        """Make sure the on-air track and the next few are on disk."""
        current = await asyncio.to_thread(self.now_playing)
        if current is None:
            return
        ids = [current["track"]["id"]]
        nxt = current["next"]
        for _ in range(self.settings.radio_prefetch_count):
            if not nxt:
                break
            ids.append(nxt["id"])
            after = self.db.radio_next_track(nxt["id"])
            nxt = _public(after) if after and after["message_id"] not in ids else None
        for mid in ids:
            await self.ensure_cached(mid)
        self.evict(keep=set(ids))

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
        connected = False
        restored = False
        next_snapshot = 0.0
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
                self.last_tick_at = time.monotonic()
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
                        if not restored:
                            restored = True
                            try:
                                await self.restore_from_snapshot()
                            except Exception as exc:
                                log.warning("Radio: could not restore the saved position: %s", exc)
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

                if (connected or self.state_store) and loop.time() >= next_snapshot:
                    next_snapshot = loop.time() + self.settings.radio_snapshot_interval_seconds
                    try:
                        await self.save_snapshot()
                    except Exception as exc:
                        log.warning("Radio: could not save the position to Telegram: %s", exc)

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
            if connected or self.state_store:
                try:
                    await asyncio.wait_for(self.save_snapshot(), timeout=5)   # last position before Render stops us
                except Exception as exc:
                    log.warning("Radio: final position save failed: %s", exc)
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
            if track is None or not track["available"]:
                return
            position_seconds = state.get("position_seconds")
            checkpoint_at = state.get("checkpoint_at")
            if checkpoint_at is None or position_seconds is None:
                return
            self._position_at_checkpoint = float(position_seconds)
            self._mono_at_checkpoint = self._mono_clock()
