"""Monitoring logic.

check_once()    one pass (used by POST /check-now and by the background loop)
run_forever()   the background loop that survives errors

Per pass:
  1. If a stream is current, re-verify it (1 quota unit). If it ended -> mark ended.
  2. If nothing is live, look for a new stream. The cheap channel check runs every pass;
     the expensive keyword search (100 units) runs at most every DISCOVERY_INTERVAL_SECONDS
     and only while enough daily quota remains.
  3. Sync WordPress so it matches our database. A failed sync is retried on the next pass,
     and the state is re-sent every WORDPRESS_RESYNC_SECONDS as a heartbeat.
"""
import asyncio
import logging
import threading
import time
from datetime import datetime, timedelta

from .config import Settings
from .database import Database
from .wordpress import WordPressClient, WordPressError
from .youtube import PACIFIC, YouTubeClient, YouTubeError, YouTubeQuotaError, find_live_streams

log = logging.getLogger("monitor")

SEARCH_COST = 100
QUOTA_RESERVE = 1500            # always keep this many units for cheap live/ended checks
WORDPRESS_RESYNC_SECONDS = 600


def backoff_delay(interval: float, failures: int, cap: float = 600.0) -> float:
    """interval*2, *4, *8 ... capped at `cap` (never below the normal interval)."""
    return min(interval * (2 ** failures), max(cap, interval))


def seconds_until_quota_reset(now: datetime | None = None) -> float:
    """Seconds until midnight Pacific (+60s margin), when YouTube resets the daily quota."""
    now = now or datetime.now(PACIFIC)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return (midnight - now).total_seconds() + 60


class Monitor:
    def __init__(self, settings: Settings, db: Database, youtube: YouTubeClient,
                 wordpress: WordPressClient | None = None, clock=time.monotonic):
        self.settings = settings
        self.db = db
        self.youtube = youtube
        self.wordpress = wordpress
        self._clock = clock
        self._last_search: float | None = None
        self._wp_synced: str | None = None       # what WordPress last confirmed: "offline" | "live:<id>"
        self._wp_last_push: float = 0.0
        self._lock = threading.Lock()   # a manual /check-now never overlaps the background pass

    # ---- helpers ---------------------------------------------------------
    def _search_due(self) -> bool:
        interval_ok = (self._last_search is None
                       or self._clock() - self._last_search >= self.settings.discovery_interval_seconds)
        budget_ok = (self.youtube.units_today + SEARCH_COST
                     <= self.settings.daily_quota_budget - QUOTA_RESERVE)
        if interval_ok and not budget_ok:
            log.warning("Skipping keyword search to preserve daily quota (%s units used).",
                        self.youtube.units_today)
        return interval_ok and budget_ok

    def _result(self, action: str) -> dict:
        current = self.db.get_current()
        return {"status": "live" if current else "offline", "action": action,
                "video_id": current["video_id"] if current else None,
                "quota_units_today": self.youtube.units_today}

    # ---- one pass --------------------------------------------------------
    def check_once(self, force_search: bool = False) -> dict:
        """Run one pass. Raises YouTubeError on API failure (database is left unchanged)."""
        with self._lock:
            try:
                result = self._check(force_search)
            except Exception:
                self._sync_wordpress()      # keep retrying a pending sync even while YouTube is failing
                raise
            result["wordpress"] = self._sync_wordpress()
            return result

    def _check(self, force_search: bool) -> dict:
        log.info("Checking YouTube...")
        action = "none"

        current = self.db.get_current()
        if current and current["minister_name"].casefold() != self.settings.minister_name.casefold():
            # MINISTER_NAME changed since this stream was saved: don't keep tracking it.
            self.db.mark_ended(current["video_id"])
            log.info("MINISTER_NAME changed to '%s'; dropped previously tracked stream %s.",
                     self.settings.minister_name, current["video_id"])
            current = None
        if current:
            if self.youtube.is_still_live(current["video_id"]):
                self.db.touch(current["video_id"])
                log.info("Still live: '%s' [%s] (%s)", current["title"],
                         current["channel_title"], current["video_id"])
                return self._result("still_live")
            self.db.mark_ended(current["video_id"])
            log.info("Livestream ended.")
            log.info("Video ID: %s", current["video_id"])
            action = "ended"

        run_search = force_search or self._search_due()
        found = find_live_streams(self.youtube, self.settings, run_search=run_search)
        if run_search:
            self._last_search = self._clock()

        if not found:
            if action == "none":
                log.info("No matching livestream found.")
            return self._result(action)

        video, match = found[0]
        if len(found) > 1:
            log.info("%d matching live streams found:", len(found))
            for v, m in found:
                log.info("  - %s '%s' [%s] score %s, %s viewers", v.video_id, v.title,
                         v.channel_title, m.score, v.concurrent_viewers)
            log.info("Chose %s. Set TRUSTED_CHANNEL_IDS to pin the official channel.", video.video_id)
        _, is_new = self.db.record_live(video, self.settings.minister_name,
                                        match.score, match.reasons)
        if is_new:
            log.info("Livestream detected.")
            log.info("Minister: %s", self.settings.minister_name)
            log.info("Channel: %s", video.channel_title)
            log.info("Video ID: %s", video.video_id)
        return self._result("detected")

    # ---- WordPress sync --------------------------------------------------
    def _sync_wordpress(self) -> str:
        """Make WordPress match our database. Returns disabled | up_to_date | synced | failed."""
        if self.wordpress is None:
            return "disabled"
        current = self.db.get_current()
        desired = f"live:{current['video_id']}" if current else "offline"
        heartbeat_due = self._clock() - self._wp_last_push >= WORDPRESS_RESYNC_SECONDS
        if desired == self._wp_synced and not heartbeat_due:
            return "up_to_date"
        changed = desired != self._wp_synced
        try:
            if current:
                self.wordpress.push_live(current)
            else:
                self.wordpress.push_offline()
        except WordPressError as exc:
            log.error("WordPress update failed: %s (will retry)", exc)
            return "failed"
        self._wp_synced = desired
        self._wp_last_push = self._clock()
        if changed:
            log.info("WordPress updated successfully." if current
                     else "WordPress status changed to offline.")
        return "synced"

    # ---- background loop -------------------------------------------------
    async def run_forever(self, stop: asyncio.Event) -> None:
        """Background loop. Never raises (except cancellation); every failure is logged and retried."""
        interval = self.settings.check_interval_seconds
        failures = 0
        log.info("Background monitor started (checking every %ss).", interval)

        while not stop.is_set():
            try:
                await asyncio.to_thread(self.check_once)   # blocking I/O off the event loop
                failures = 0
                delay = interval
            except YouTubeQuotaError as exc:
                delay = seconds_until_quota_reset()
                log.error("YouTube quota exhausted (%s). Pausing %d minutes until it resets.",
                          exc, delay // 60)
            except YouTubeError as exc:
                failures += 1
                delay = backoff_delay(interval, failures)
                log.error("YouTube check failed: %s. Retrying in %ds.", exc, delay)
            except Exception:   # database error, bug, anything: keep the service alive
                failures += 1
                delay = backoff_delay(interval, failures)
                log.exception("Unexpected error during check. Retrying in %ds.", delay)

            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)   # sleeps, but wakes on shutdown
            except asyncio.TimeoutError:
                pass

        log.info("Background monitor stopped.")