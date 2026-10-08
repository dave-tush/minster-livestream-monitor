"""FastAPI application."""
import asyncio
import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse

from .config import Settings
from .database import Database
from .monitor import Monitor
from .wordpress import WordPressClient
from .youtube import YouTubeClient, YouTubeError

log = logging.getLogger("monitor")

HOME_PAGE = """<!doctype html>
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
      .badge.warn { background: #fff4e0; color: #9a5b00; }
      .dot { background: #20a35b; border-radius: 50%; display: inline-block;
             height: 8px; margin-right: 7px; width: 8px; }
      .warn .dot { background: #e08a00; }
      h1 { font-size: 25px; margin: 22px 0 8px; }
      p { color: #647084; line-height: 1.6; margin: 0; }
      code { background: #f3f6fa; border-radius: 5px; padding: 2px 6px; }
    </style>
  </head>
  <body>
    <main>
      <span class="badge __BADGE_CLASS__"><span class="dot"></span>Server is running</span>
      <h1>Minister Livestream Monitor</h1>
      <p>__MONITOR_TEXT__ Check <code>/health</code> for the health endpoint.</p>
    </main>
  </body>
</html>"""


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
        if mon is None and cfg.minister_name and cfg.youtube_api_key:
            db = Database(cfg.database_url)
            db.init_schema()
            wp = None
            if cfg.wordpress_url and cfg.wordpress_api_key:
                wp = WordPressClient(cfg.wordpress_url, cfg.wordpress_api_key)
                log.info("WordPress integration enabled: %s", cfg.wordpress_url)
            else:
                log.warning("WordPress integration DISABLED (set WORDPRESS_URL and WORDPRESS_API_KEY).")
            mon = Monitor(cfg, db, YouTubeClient(cfg.youtube_api_key), wordpress=wp)
        app.state.settings = cfg
        app.state.monitor = mon

        if mon is None:
            log.warning("Service started WITHOUT monitoring. Set MINISTER_NAME and YOUTUBE_API_KEY.")
        else:
            log.info("Service started. Monitoring for: %s", cfg.minister_name)
            saved = mon.db.get_current()
            if saved:
                log.info("Resuming tracked stream from a previous run: '%s' (%s). "
                         "Delete data/monitor.db to start fresh.", saved["title"], saved["video_id"])

        stop = asyncio.Event()
        task = asyncio.create_task(mon.run_forever(stop)) if (run_background and mon) else None
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

    @app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
    def home(request: Request):
        if request.app.state.monitor is not None:
            badge, text = "", "Monitoring is active."
        else:
            badge, text = "warn", "Monitoring is NOT configured yet (missing MINISTER_NAME or YOUTUBE_API_KEY)."
        return HOME_PAGE.replace("__BADGE_CLASS__", badge).replace("__MONITOR_TEXT__", text)

    @app.api_route("/health", methods=["GET", "HEAD"])
    def health():
        return {"status": "ok"}

    @app.get("/status")
    def status(request: Request):
        mon = request.app.state.monitor
        if mon is None:
            return {"status": "unconfigured"}
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
        mon = request.app.state.monitor
        if mon is None:
            raise HTTPException(503, "Monitoring is not configured (MINISTER_NAME / YOUTUBE_API_KEY)")
        try:
            return mon.check_once(force_search=force_search)
        except YouTubeError as exc:
            log.error("Manual check failed: %s", exc)
            raise HTTPException(502, "YouTube API error; see server logs") from None

    return app


app = create_app()