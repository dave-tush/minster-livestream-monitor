"""Stage 3: FastAPI application."""
import asyncio
import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request

from .config import Settings
from .database import Database
from .monitor import Monitor
from .youtube import YouTubeClient, YouTubeError

log = logging.getLogger("monitor")


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)   # httpx logs full URLs (incl. API key)


def create_app(settings: Settings | None = None, monitor: Monitor | None = None,
               background: bool | None = None) -> FastAPI:
    """Factory. Tests inject `settings`/`monitor` (no background loop unless background=True);
    production builds everything from the environment and runs the loop."""
    run_background = (monitor is None) if background is None else background

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        setup_logging()
        cfg = settings or Settings.from_env()
        mon = monitor
        if mon is None:
            cfg.require_youtube()
            db = Database(cfg.database_url)
            db.init_schema()
            mon = Monitor(cfg, db, YouTubeClient(cfg.youtube_api_key))
        app.state.settings = cfg
        app.state.monitor = mon
        log.info("Service started. Monitoring for: %s", cfg.minister_name)
        saved = mon.db.get_current()
        if saved:
            log.info("Resuming tracked stream from a previous run: '%s' (%s). "
                     "Delete data/monitor.db to start fresh.", saved["title"], saved["video_id"])

        stop = asyncio.Event()
        task = asyncio.create_task(mon.run_forever(stop)) if run_background else None
        yield
        if task:
            stop.set()
            try:
                await asyncio.wait_for(task, timeout=10)
            except asyncio.TimeoutError:
                task.cancel()

    app = FastAPI(title="Minister Livestream Monitor", lifespan=lifespan)

    def require_admin(request: Request, x_api_key: str | None) -> None:
        expected = request.app.state.settings.admin_api_key
        if not expected:
            raise HTTPException(503, "Endpoint disabled: ADMIN_API_KEY is not configured")
        if not x_api_key or not secrets.compare_digest(x_api_key.encode(), expected.encode()):
            raise HTTPException(401, "Invalid API key")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/status")
    def status(request: Request):
        current = request.app.state.monitor.db.get_current()
        if not current:
            return {"status": "offline"}
        return {
            "status": "live",
            "video_id": current["video_id"],
            "title": current["title"],
            "channel_id": current["channel_id"],
            "channel_title": current["channel_title"],
            "youtube_url": current["youtube_url"],
            "thumbnail_url": current["thumbnail_url"],
            "started_at": current["started_at"],
        }

    # Sync `def` on purpose: FastAPI runs it in a worker thread, so the blocking
    # YouTube/SQLite calls don't stall the event loop.
    @app.post("/check-now")
    def check_now(request: Request, x_api_key: str | None = Header(default=None),
                  force_search: bool = False):
        require_admin(request, x_api_key)
        try:
            return request.app.state.monitor.check_once(force_search=force_search)
        except YouTubeError as exc:
            log.error("Manual check failed: %s", exc)
            raise HTTPException(502, "YouTube API error; see server logs") from None

    return app


app = create_app()