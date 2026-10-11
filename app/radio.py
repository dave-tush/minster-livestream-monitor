"""Dominion Radio: a 24/7 radio station built from the audio posts in a Telegram channel.

How it works
------------
* SOURCE   A Telegram *user* session (Telethon / MTProto) reads the channel. The plain Bot API
           cannot read old posts and caps downloads at 20 MB, so it cannot start "from the beginning".
* PLAYLIST Every audio/voice post becomes a track, ordered by message_id = oldest upload first.
           New uploads are picked up by a periodic sync and join the end of the queue.
* CLOCK    The station keeps ONE "on air" pointer (track + the moment it started), stored in SQLite.
           Position is always computed from the wall clock, so every listener hears the same
           thing at the same moment, like a real radio, and it keeps "broadcasting" through restarts.
           When the last track finishes, the station loops back to the first.
* FILES    Audio is downloaded lazily into a small disk cache (current + next few tracks) and old
           files are evicted, so a big channel does not fill the disk.
"""
import asyncio
import logging
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

from .config import Settings
from .database import Database

log = logging.getLogger("radio")

TICK_SECONDS = 15           # how often the background loop wakes up


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
        max_seen = min_id
        async for msg in self._client.iter_messages(self._entity, min_id=min_id, reverse=True):
            max_seen = max(max_seen, msg.id)
            f = msg.file
            if f is None or not (msg.audio or msg.voice or (f.mime_type or "").startswith("audio/")):
                continue
            if not f.duration:
                log.warning("Skipping post %s: Telegram gave no duration for it.", msg.id)
                continue
            caption = (msg.message or "").strip().splitlines()
            title = (f.title or (caption[0] if caption else "")
                     or (Path(f.name).stem if f.name else "") or f"Track {msg.id}")
            tracks.append(TrackMeta(
                message_id=msg.id, title=title[:200], duration=int(f.duration),
                performer=(f.performer or "")[:200], file_size=f.size or 0,
                mime_type=f.mime_type or "audio/mpeg", ext=_safe_ext(f.ext),
                posted_at=msg.date.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")))
        return tracks, max_seen

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
                 clock=lambda: datetime.now(timezone.utc)):
        self.settings, self.db, self.source, self._clock = settings, db, source, clock
        self.cache_dir = Path(settings.radio_cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()                  # serialises on-air pointer updates
        self._dl_locks: dict[int, asyncio.Lock] = {}
        self.last_error: str | None = None
        self.last_sync_at: str | None = None

    # ---- the on-air clock ------------------------------------------------
    def now_playing(self, now: datetime | None = None) -> dict | None:
        """What is on air right now (advances the pointer if the track has finished)."""
        now = now or self._clock()
        with self._lock:
            track, started = self._advance(now)
            if track is None:
                return None
            position = max(0.0, (now - started).total_seconds())
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

    def _advance(self, now: datetime) -> tuple[dict | None, datetime]:
        state = self.db.radio_get_state()
        track = self.db.radio_get_track(state["message_id"]) if state["message_id"] else None
        if track is None or not track["available"] or not state["started_at"]:
            track = self.db.radio_first_track()      # first run, or the on-air track was removed
            if track is None:
                return None, now
            started = now
        else:
            started = _parse(state["started_at"])
        if started > now:                            # clock moved backwards: restart this track
            started = now

        # While the server was off the station kept "broadcasting": skip whole loops of the
        # playlist instead of stepping through every track.
        total = self.db.radio_total_seconds()
        elapsed = (now - started).total_seconds()
        if total > 0 and elapsed >= total:
            started += timedelta(seconds=(elapsed // total) * total)

        for _ in range(self.db.radio_count() + 1):
            if (now - started).total_seconds() < track["duration"]:
                break
            started += timedelta(seconds=track["duration"])
            track = self.db.radio_next_track(track["message_id"]) or track

        if (track["message_id"] != state["message_id"]
                or _fmt(started) != state["started_at"]):
            self.db.radio_set_state(track["message_id"], _fmt(started))
        return track, started

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
        """
        key = self._channel_key(self.settings.telegram_channel)
        if not key:
            return
        saved = self.db.radio_meta_get("channel")
        if saved == key:
            return
        if saved is not None or self.db.radio_has_tracks():     # other channel, or history of unknown origin
            log.warning("Radio: Telegram channel is now '%s' (was '%s'). Clearing the old playlist and "
                        "starting this channel from its first upload.", key, saved or "unknown")
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
        return added

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
            except Exception as exc:
                part.unlink(missing_ok=True)
                log.error("Radio: download of %s failed: %s", message_id, exc)
                if isinstance(exc, FileNotFoundError):
                    self.db.radio_set_available(message_id, False)   # post was deleted
                return None
        return path

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
        next_sync = 0.0
        loop = asyncio.get_running_loop()
        failures = 0
        while not stop.is_set():
            delay = TICK_SECONDS
            try:
                if not connected:
                    await self.source.connect()
                    connected = True
                if loop.time() >= next_sync:
                    await self.sync_once()
                    next_sync = loop.time() + self.settings.radio_sync_interval_seconds
                await self.prefetch()
                self.last_error, failures = None, 0
            except Exception as exc:
                failures += 1
                self.last_error = str(exc)
                delay = min(TICK_SECONDS * (2 ** failures), 600)
                log.error("Radio error: %s. Retrying in %ds.", exc, delay)
                if connected:                       # reconnect from scratch after a failure
                    try:
                        await self.source.disconnect()
                    except Exception:
                        pass
                    connected = False
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
        try:
            await self.source.disconnect()
        except Exception:
            pass
        log.info("Radio stopped.")
