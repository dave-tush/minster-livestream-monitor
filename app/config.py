"""Configuration loaded from environment variables (.env). No secrets in code."""
import json
import os
from dataclasses import dataclass, field

from dotenv import dotenv_values, find_dotenv, load_dotenv

# The .env file WINS over variables already set in your terminal. (python-dotenv's default is the
# opposite, which silently ignores edits to .env if the same name was ever exported in the shell.)
# Names whose terminal value differed from .env are kept so the app can warn about them at startup.
ENV_OVERRIDES: list[str] = []
_ENV_FILE = find_dotenv()
if _ENV_FILE:
    ENV_OVERRIDES = [k for k, v in dotenv_values(_ENV_FILE).items()
                     if v is not None and k in os.environ and os.environ[k] != v]
    load_dotenv(_ENV_FILE, override=True)


def _parse_list(raw: str | None) -> list[str]:
    raw = (raw or "").strip()
    if not raw:
        return []
    if raw.startswith("["):
        try:
            return [str(x).strip() for x in json.loads(raw) if str(x).strip()]
        except json.JSONDecodeError:
            pass
    return [p.strip() for p in raw.split(",") if p.strip()]


def _parse_bool(raw: str | None, default: bool) -> bool:
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    minister_name: str
    youtube_api_key: str = field(repr=False)
    wordpress_url: str = ""
    wordpress_api_key: str = field(default="", repr=False)
    admin_api_key: str = field(default="", repr=False)
    trusted_channel_ids: list[str] = field(default_factory=list)
    trusted_only: bool = False
    check_interval_seconds: int = 60
    discovery_interval_seconds: int = 1800
    daily_quota_budget: int = 10000
    database_url: str = "sqlite:///./data/monitor.db"
    # Channels to look at for candidate streams (the churches he visits). Unlike TRUSTED_CHANNEL_IDS,
    # their streams are NOT accepted automatically: the name must still be proven (thumbnail/title).
    watch_channel_ids: list[str] = field(default_factory=list)
    extra_search_queries: list[str] = field(default_factory=list)
    # --- Thumbnail verification (reads the name printed on the stream's thumbnail) ---
    thumbnail_ocr: bool = False
    thumbnail_ocr_engine: str = "auto"
    thumbnail_ocr_max_per_check: int = 6
    minister_aliases: list[str] = field(default_factory=list)
    # --- Transcript check: when does the minister come up? ---
    transcript_check: bool = False
    transcript_interval_seconds: int = 300
    transcript_max_checks: int = 60
    transcript_require_thumbnail: bool = True
    transcript_languages: list[str] = field(default_factory=lambda: ["en", "en-US", "en-GB"])
    ministration_keywords: list[str] = field(default_factory=list)
    # --- Dominion Radio (Telegram) ---
    radio_name: str = "Dominion Radio"
    telegram_api_id: int = 0
    telegram_api_hash: str = field(default="", repr=False)
    telegram_session_string: str = field(default="", repr=False)
    telegram_channel: str = ""
    radio_cache_dir: str = "./data/radio"
    radio_cache_max_mb: int = 1024
    radio_sync_interval_seconds: int = 300
    radio_prefetch_count: int = 2

    @property
    def radio_configured(self) -> bool:
        return bool(self.telegram_api_id and self.telegram_api_hash
                    and self.telegram_session_string and self.telegram_channel)

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            minister_name=os.getenv("MINISTER_NAME", "").strip(),
            youtube_api_key=os.getenv("YOUTUBE_API_KEY", "").strip(),
            wordpress_url=os.getenv("WORDPRESS_URL", "").rstrip("/"),
            wordpress_api_key=os.getenv("WORDPRESS_API_KEY", "").strip(),
            admin_api_key=os.getenv("ADMIN_API_KEY", "").strip(),
            trusted_channel_ids=_parse_list(os.getenv("TRUSTED_CHANNEL_IDS")),
            trusted_only=_parse_bool(os.getenv("TRUSTED_ONLY"), False),
            check_interval_seconds=max(int(os.getenv("CHECK_INTERVAL_SECONDS", "60")), 15),
            discovery_interval_seconds=max(int(os.getenv("DISCOVERY_INTERVAL_SECONDS", "1800")), 60),
            daily_quota_budget=int(os.getenv("DAILY_QUOTA_BUDGET", "10000")),
            database_url=os.getenv("DATABASE_URL", "sqlite:///./data/monitor.db"),
            watch_channel_ids=_parse_list(os.getenv("WATCH_CHANNEL_IDS")),
            extra_search_queries=_parse_list(os.getenv("EXTRA_SEARCH_QUERIES")),
            thumbnail_ocr=_parse_bool(os.getenv("THUMBNAIL_OCR"), True),
            thumbnail_ocr_engine=os.getenv("THUMBNAIL_OCR_ENGINE", "auto").strip().lower() or "auto",
            thumbnail_ocr_max_per_check=max(int(os.getenv("THUMBNAIL_OCR_MAX_PER_CHECK", "6")), 1),
            minister_aliases=_parse_list(os.getenv("MINISTER_ALIASES")),
            transcript_check=_parse_bool(os.getenv("TRANSCRIPT_CHECK"), True),
            transcript_interval_seconds=max(int(os.getenv("TRANSCRIPT_INTERVAL_SECONDS", "300")), 60),
            transcript_max_checks=max(int(os.getenv("TRANSCRIPT_MAX_CHECKS", "60")), 1),
            transcript_require_thumbnail=_parse_bool(os.getenv("TRANSCRIPT_REQUIRE_THUMBNAIL"), True),
            transcript_languages=_parse_list(os.getenv("TRANSCRIPT_LANGUAGES")) or ["en", "en-US", "en-GB"],
            ministration_keywords=_parse_list(os.getenv("MINISTRATION_KEYWORDS")),
            radio_name=os.getenv("RADIO_NAME", "Dominion Radio").strip() or "Dominion Radio",
            telegram_api_id=int(os.getenv("TELEGRAM_API_ID", "0") or 0),
            telegram_api_hash=os.getenv("TELEGRAM_API_HASH", "").strip(),
            telegram_session_string=os.getenv("TELEGRAM_SESSION_STRING", "").strip(),
            telegram_channel=os.getenv("TELEGRAM_CHANNEL", "").strip(),
            radio_cache_dir=os.getenv("RADIO_CACHE_DIR", "./data/radio"),
            radio_cache_max_mb=max(int(os.getenv("RADIO_CACHE_MAX_MB", "1024")), 50),
            radio_sync_interval_seconds=max(int(os.getenv("RADIO_SYNC_INTERVAL_SECONDS", "300")), 30),
            radio_prefetch_count=max(int(os.getenv("RADIO_PREFETCH_COUNT", "2")), 0),
        )

    def require_youtube(self) -> None:
        missing = [n for n, v in (("MINISTER_NAME", self.minister_name),
                                  ("YOUTUBE_API_KEY", self.youtube_api_key)) if not v]
        if missing:
            raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")
