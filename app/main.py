"""FastAPI application."""
import asyncio
import html
import logging
import secrets
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from .config import ENV_OVERRIDES, Settings
from .d1 import D1StateStore
from .database import Database
from .keepalive import KeepAliveStatus, Watchdog, keep_alive_loop
from .monitor import Monitor
from .radio import RadioEngine, TelethonSource
from .radio_routes import build_radio_router
from .thumbnail import ThumbnailReader, make_engine
from .transcript import YouTubeCaptions
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
      __RADIO_LINK__
    </main>
  </body>
</html>"""


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)   # httpx logs full URLs (incl. API key)


def create_app(settings: Settings | None = None, monitor: Monitor | None = None,
               background: bool | None = None, radio: RadioEngine | None = None) -> FastAPI:
    """Factory. Tests inject `settings`/`monitor` (no background loop unless background=True);
    production builds everything from the environment and runs the loop."""
    run_background = (monitor is None) if background is None else background
    run_radio = (radio is None) if background is None else background   # tests inject a radio and drive it by hand

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        setup_logging()
        cfg = settings or Settings.from_env()
        if settings is None and ENV_OVERRIDES:
            log.warning("These variables were already set in your terminal with different values; "
                        "the .env file was used instead: %s. To clear them from the terminal run: unset %s",
                        ", ".join(ENV_OVERRIDES), " ".join(ENV_OVERRIDES))
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
            reader = None
            if cfg.thumbnail_ocr:
                reader = ThumbnailReader(make_engine(cfg.thumbnail_ocr_engine),
                                         cfg.thumbnail_ocr_max_per_check)
                log.info("Thumbnail verification enabled (engine: %s).", cfg.thumbnail_ocr_engine)
            captions = None
            if cfg.transcript_check:
                captions = YouTubeCaptions(cfg.transcript_languages)
                log.info("Transcript check enabled (every %ss, %s).", cfg.transcript_interval_seconds,
                         "after the thumbnail confirms the minister" if cfg.transcript_require_thumbnail
                         else "for every matched stream")
            mon = Monitor(cfg, db, YouTubeClient(cfg.youtube_api_key), wordpress=wp, thumbnails=reader,
                          transcripts=captions)
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

        # ---- Dominion Radio (optional: needs the TELEGRAM_* settings) ----
        rad = radio
        if rad is None and cfg.radio_configured:
            rdb = mon.db if mon else Database(cfg.database_url)
            rdb.init_schema()
            store = (D1StateStore(cfg.cloudflare_account_id, cfg.cloudflare_d1_database_id,
                                  cfg.cloudflare_api_token) if cfg.d1_configured else None)
            log.info("Cloudflare D1 position storage: %s.", "ON" if store else "off (set CLOUDFLARE_* to enable)")
            rad = RadioEngine(cfg, rdb, TelethonSource(cfg.telegram_api_id, cfg.telegram_api_hash,
                                                       cfg.telegram_session_string, cfg.telegram_channel,
                                                       include_no_duration=cfg.radio_include_no_duration),
                              state_store=store)
            log.info("Radio enabled: %s (channel %s)", cfg.radio_name, cfg.telegram_channel)
        elif rad is None:
            log.warning("Radio DISABLED (set TELEGRAM_API_ID, TELEGRAM_API_HASH, "
                        "TELEGRAM_SESSION_STRING and TELEGRAM_CHANNEL).")
        app.state.radio = rad

        stop = asyncio.Event()
        ka = KeepAliveStatus(cfg.keep_alive_url if run_background else "", cfg.keep_alive_interval_seconds)
        app.state.keepalive = ka
        dog = Watchdog(stop, ka)
        if run_background and mon:
            dog.start("monitor", lambda: mon.run_forever(stop))
        if rad and run_radio:
            dog.start("radio", lambda: rad.run_forever(stop),
                      stale=lambda: time.monotonic() - rad.last_tick_at)
        extra = []
        if run_background and (mon or rad):
            extra.append(asyncio.create_task(dog.run()))
        if ka.url:
            extra.append(asyncio.create_task(keep_alive_loop(ka, stop)))
        elif run_background:
            log.warning("Keep-alive OFF: no KEEP_ALIVE_URL or RENDER_EXTERNAL_URL. "
                        "Use an external pinger on /health to stop Render sleeping.")
        yield
        stop.set()
        await dog.cancel_all()
        for t in extra:
            try:
                await asyncio.wait_for(t, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                t.cancel()

    app = FastAPI(title="Minister Livestream Monitor", lifespan=lifespan)

    # CORS for the Dominion Radio WordPress frontend.
    # Only the production WordPress origin is allowed; no wildcard.
    app.add_middleware(
        CORSMiddleware,
        # allow_origins=["https://toluwalogoagboola.org"],
        allow_origins=["https://toluwalogoagboola.org", "https://rejectbrothers.s6-tastewp.com/radio/", "https://rejectbrothers.s6-tastewp.com"],
        allow_origin_regex=None,
        allow_methods=["GET", "OPTIONS"],
        allow_headers=["Accept", "Accept-Language", "Cache-Control", "Content-Language", "Content-Type", "Range"],
        allow_credentials=False,
        max_age=86400,
        expose_headers=["Accept-Ranges", "Content-Range", "Content-Length", "Content-Type"],
    )

    app.include_router(build_radio_router())

    def require_admin(request: Request, x_api_key: str | None) -> None:
        expected = request.app.state.settings.admin_api_key
        if not expected:
            raise HTTPException(503, "Endpoint disabled: ADMIN_API_KEY is not configured")
        if not x_api_key or not secrets.compare_digest(x_api_key.encode(), expected.encode()):
            raise HTTPException(401, "Invalid API key")

    @app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
    def home(request: Request):
        if request.app.state.monitor is not None:
            badge = ""
            text = f"Monitoring is active for: <strong>{html.escape(request.app.state.settings.minister_name)}</strong>."
        else:
            badge, text = "warn", "Monitoring is NOT configured yet (missing MINISTER_NAME or YOUTUBE_API_KEY)."
        rad = request.app.state.radio
        link = (f'<p style="margin-top:14px"><a href="/radio">&#127911; Listen to '
                f'{html.escape(rad.settings.radio_name)}</a></p>') if rad else ""
        return (HOME_PAGE.replace("__BADGE_CLASS__", badge).replace("__MONITOR_TEXT__", text)
                .replace("__RADIO_LINK__", link))

    @app.api_route("/health", methods=["GET", "HEAD"])
    def health():
        return {"status": "ok"}

    @app.get("/health/detail")
    def health_detail(request: Request):
        """Is each background loop alive? Useful for the uptime pinger and for debugging."""
        rad, ka = request.app.state.radio, getattr(request.app.state, "keepalive", None)
        out = {"status": "ok", "keepalive": ka.as_dict() if ka else {"enabled": False}}
        if rad is not None:
            out["radio"] = {"tracks": rad.db.radio_count(), "last_error": rad.last_error,
                            "last_sync_at": rad.last_sync_at, "last_snapshot_at": rad.last_snapshot_at,
                            "resumed_after_restart": rad.restored_from_snapshot,
                            "d1": rad.state_store is not None,
                            "seconds_since_loop_progress": round(time.monotonic() - rad.last_tick_at)}
        return out

    @app.get("/status")
    def status(request: Request):
        mon = request.app.state.monitor
        if mon is None:
            return {"status": "unconfigured"}
        current = mon.db.get_current()
        if not current:
            return {"status": "offline"}
        payload = {
            "status": "live",
            "video_id": current["video_id"],
            "title": current["title"],
            "channel_id": current["channel_id"],
            "channel_title": current["channel_title"],
            "youtube_url": current["youtube_url"],
            "thumbnail_url": current["thumbnail_url"],
            "started_at": current["started_at"],
        }
        if mon.transcripts is not None:       # only present when the transcript check is on
            payload["ministration"] = mon.ministration_for(current)
        return payload

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