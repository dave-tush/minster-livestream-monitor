"""Test Dominion Radio without starting the server:

    python -m app.radio_check              # connect, scan the channel, download one track, show what is on air
    python -m app.radio_check --no-download  # skip the download step

Each step prints OK or FAILED with the reason, so you can see exactly what to fix.
Nothing is written to your real database or cache (a temporary folder is used).
"""
import asyncio
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from .config import Settings
from .database import Database
from .radio import RadioEngine, TelethonSource


def _hms(seconds: int) -> str:
    h, rem = divmod(int(seconds), 3600)
    return f"{h}h {rem // 60:02d}m"


async def run_check(settings: Settings, source=None, download: bool = True, out=print) -> bool:
    """Returns True if every step passed. `source` lets tests plug in a fake Telegram."""
    ok = lambda msg: out(f"  OK      {msg}")
    fail = lambda msg: out(f"  FAILED  {msg}")

    out("\n1. Settings")
    missing = [name for name, value in (
        ("TELEGRAM_API_ID", settings.telegram_api_id), ("TELEGRAM_API_HASH", settings.telegram_api_hash),
        ("TELEGRAM_SESSION_STRING", settings.telegram_session_string),
        ("TELEGRAM_CHANNEL", settings.telegram_channel)) if not value]
    if source is None and missing:
        fail("missing in .env: " + ", ".join(missing))
        out("          (get the session string with: python scripts/telegram_login.py)")
        return False
    ok(f"channel = {settings.telegram_channel or '(test source)'}")

    source = source or TelethonSource(settings.telegram_api_id, settings.telegram_api_hash,
                                      settings.telegram_session_string, settings.telegram_channel)
    with tempfile.TemporaryDirectory() as tmp:
        settings = replace(settings, radio_cache_dir=str(Path(tmp) / "cache"))
        db = Database(f"sqlite:///{tmp}/check.db")
        db.init_schema()
        engine = RadioEngine(settings, db, source)

        out("\n2. Connecting to Telegram")
        try:
            await source.connect()
            ok("connected and the channel was found")
        except Exception as exc:
            fail(f"{type(exc).__name__}: {exc}")
            out("          Check the session string, and that this Telegram account has joined the channel.")
            return False

        try:
            out("\n3. Scanning the channel for audio (a big channel can take a minute)")
            try:
                added = await engine.sync_once()
            except Exception as exc:
                fail(f"{type(exc).__name__}: {exc}")
                return False
            if added == 0:
                fail("no playable audio found. Posts must be audio or voice messages that Telegram "
                     "gives a duration for (files sent as plain documents may not count).")
                return False
            tracks = db.radio_list_tracks(limit=100000)
            total = db.radio_total_seconds()
            ok(f"{added} tracks, {_hms(total)} of audio before the station repeats")
            out("          Oldest (plays first):")
            for t in tracks[:3]:
                out(f"            - {t['title']}  [{t['duration'] // 60}:{t['duration'] % 60:02d}]")
            if len(tracks) > 3:
                out("          Newest (plays last):")
                for t in tracks[-3:]:
                    out(f"            - {t['title']}  [{t['duration'] // 60}:{t['duration'] % 60:02d}]")

            out("\n4. What would be on air right now")
            now = engine.now_playing()
            ok(f"'{now['track']['title']}' at {int(now['position'])}s of {now['track']['duration']}s; "
               f"next: {now['next']['title'] if now['next'] else 'nothing (single track)'}")

            out("\n5. Downloading the on-air track")
            if not download:
                out("  skipped (--no-download)")
            else:
                path = await engine.ensure_cached(now["track"]["id"])
                if path is None or not path.exists() or path.stat().st_size == 0:
                    fail("the download failed. See the error logged above.")
                    return False
                expected = db.radio_get_track(now["track"]["id"])["file_size"]
                size = path.stat().st_size
                if expected and size != expected:
                    fail(f"downloaded {size} bytes but Telegram says the file is {expected} bytes")
                    return False
                ok(f"downloaded {size / 1024 / 1024:.1f} MB ({path.suffix} file). "
                   "The audio itself is ready to be played.")
        finally:
            try:
                await source.disconnect()
            except Exception:
                pass
    out("\nAll steps passed. Start the server (python run.py) and open http://localhost:8000/radio")
    return True


if __name__ == "__main__":
    good = asyncio.run(run_check(Settings.from_env(), download="--no-download" not in sys.argv))
    sys.exit(0 if good else 1)
