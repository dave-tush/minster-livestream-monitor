"""Stage 3: FastAPI application."""
import asyncio
import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse

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
            if cfg.minister_name and cfg.youtube_api_key:
                db = Database(cfg.database_url)
                db.init_schema()
                mon = Monitor(cfg, db, YouTubeClient(cfg.youtube_api_key))
        app.state.settings = cfg
        app.state.monitor = mon
        if mon is None:
            log.warning("Service started without monitoring. Configure MINISTER_NAME and "
                        "YOUTUBE_API_KEY to enable it.")
        else:
            log.info("Service started. Monitoring for: %s", cfg.minister_name)
            saved = mon.db.get_current()
            if saved:
                log.info("Resuming tracked stream from a previous run: '%s' (%s). "
                         "Delete data/monitor.db to start fresh.", saved["title"], saved["video_id"])

        stop = asyncio.Event()
        task = asyncio.create_task(mon.run_forever(stop)) if run_background and mon else None
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

    @app.get("/", response_class=HTMLResponse)
    def home():
        return """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Minister Livestream Monitor</title>
    <style>
      :root { color-scheme: light; font-family: system-ui, sans-serif; }
      body { align-items: center; background: #f3f6fa; color: #172033; display: flex;
             justify-content: center; margin: 0; min-height: 100vh; }
      main { background: white; border: 1px solid #e2e8f0; border-radius: 16px;
             box-shadow: 0 12px 36px #17203312; max-width: 440px; padding: 36px;
             text-align: center; width: calc(100% - 48px); }
      .badge { background: #e8f8ef; border-radius: 999px; color: #167443;
               display: inline-block; font-size: 14px; font-weight: 650;
               padding: 8px 14px; }
      .dot { background: #20a35b; border-radius: 50%; display: inline-block;
             height: 8px; margin-right: 7px; width: 8px; }
      h1 { font-size: 25px; margin: 22px 0 8px; }
      p { color: #647084; line-height: 1.6; margin: 0; }
      code { background: #f3f6fa; border-radius: 5px; padding: 2px 6px; }
    </style>
  </head>
  <body>
    <main>
      <span class="badge"><span class="dot"></span>Server is running</span>
      <h1>Minister Livestream Monitor</h1>
      <p>The service is online and responding. Check <code>/health</code> for the health endpoint.</p>
    </main>
  </body>
</html>"""

    @app.get("/status")
    def status(request: Request):
        mon = request.app.state.monitor
        if mon is None:
            return {"status": "offline"}
        current = mon.db.get_current()
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
        if request.app.state.monitor is None:
            raise HTTPException(503, "Monitoring is disabled: configure MINISTER_NAME and YOUTUBE_API_KEY")
        try:
            return request.app.state.monitor.check_once(force_search=force_search)
        except YouTubeError as exc:
            log.error("Manual check failed: %s", exc)
            raise HTTPException(502, "YouTube API error; see server logs") from None

    return app


app = create_app()