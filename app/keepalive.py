"""Keep the service running on Render's free plan.

Two jobs, both started from the app's lifespan:

* keep_alive_loop  pings this service's own public URL so Render sees inbound traffic and does not
                   spin it down after 15 idle minutes.
* Watchdog         restarts the monitor / radio background loops if one of them dies or hangs, so a
                   single bug cannot silently stop the station while the web server stays up.

A self-ping cannot wake a service that is already asleep, so also point a free external pinger
(UptimeRobot, cron-job.org) at <your-url>/health every 5 minutes. See the README.
"""
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable

import httpx

log = logging.getLogger("keepalive")


class KeepAliveStatus:
    def __init__(self, url: str, interval: int):
        self.url, self.interval = url, interval
        self.last_ok_at: str | None = None
        self.last_error: str | None = None
        self.pings = 0
        self.failures_in_a_row = 0
        self.restarts: dict[str, int] = {}

    def as_dict(self) -> dict:
        return {"enabled": bool(self.url), "url": self.url, "interval_seconds": self.interval,
                "pings": self.pings, "last_ok_at": self.last_ok_at, "last_error": self.last_error,
                "restarts": self.restarts}


async def keep_alive_loop(status: KeepAliveStatus, stop: asyncio.Event) -> None:
    """GET {url}/health every `interval` seconds. Never raises (except cancellation)."""
    if not status.url:
        return
    target = status.url.rstrip("/") + "/health"
    log.info("Keep-alive on: pinging %s every %ss.", target, status.interval)
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": "dominion-radio-keepalive/2.0"}) as client:
        while not stop.is_set():
            delay = status.interval
            try:
                r = await client.get(target)
                if r.status_code >= 500:
                    raise RuntimeError(f"HTTP {r.status_code}")
                status.pings += 1
                status.failures_in_a_row = 0
                status.last_error = None
                status.last_ok_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception as exc:
                status.failures_in_a_row += 1
                status.last_error = f"{type(exc).__name__}: {exc}"
                delay = min(status.interval, 30)          # try again soon instead of waiting 4 minutes
                if status.failures_in_a_row in (1, 5) or status.failures_in_a_row % 20 == 0:
                    log.warning("Keep-alive ping failed (%d in a row): %s", status.failures_in_a_row, status.last_error)
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass


class Watchdog:
    """Keeps named background tasks alive. `start(name, factory, stale)`:
    factory() returns the coroutine to run; stale() -> seconds since it last made progress (or None)."""

    def __init__(self, stop: asyncio.Event, status: KeepAliveStatus, check_seconds: float = 30,
                 stale_after: float = 900):
        self.stop, self.status = stop, status
        self.check_seconds, self.stale_after = check_seconds, stale_after
        self._jobs: dict[str, tuple[Callable[[], Awaitable], Callable[[], float | None] | None]] = {}
        self.tasks: dict[str, asyncio.Task] = {}

    def start(self, name: str, factory, stale=None) -> None:
        self._jobs[name] = (factory, stale)
        self.tasks[name] = asyncio.create_task(factory(), name=name)

    async def run(self) -> None:
        while not self.stop.is_set():
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=self.check_seconds)
                return
            except asyncio.TimeoutError:
                pass
            for name, (factory, stale) in self._jobs.items():
                task = self.tasks[name]
                reason = None
                if task.done():
                    exc = None if task.cancelled() else task.exception()
                    reason = f"stopped ({exc!r})" if exc else "stopped"
                elif stale is not None:
                    age = stale()
                    if age is not None and age > self.stale_after:
                        reason = f"no progress for {int(age)}s"
                        task.cancel()
                        try:
                            await asyncio.wait_for(task, timeout=10)
                        except BaseException:
                            pass
                if reason:
                    log.error("Watchdog: %s %s. Restarting it.", name, reason)
                    self.status.restarts[name] = self.status.restarts.get(name, 0) + 1
                    self.tasks[name] = asyncio.create_task(factory(), name=name)

    async def cancel_all(self, timeout: float = 10) -> None:
        for t in self.tasks.values():
            try:
                await asyncio.wait_for(t, timeout=timeout)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                t.cancel()
            except Exception:
                pass
