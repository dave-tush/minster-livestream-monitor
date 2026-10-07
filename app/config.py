"""Configuration loaded from environment variables (.env). No secrets in code."""
import json
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


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
        )

    def require_youtube(self) -> None:
        missing = [n for n, v in (("MINISTER_NAME", self.minister_name),
                                  ("YOUTUBE_API_KEY", self.youtube_api_key)) if not v]
        if missing:
            raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")
