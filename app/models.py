"""Plain data objects shared across modules."""
from dataclasses import dataclass


@dataclass
class LiveVideo:
    video_id: str
    title: str
    channel_id: str
    channel_title: str
    description: str = ""
    broadcast_status: str = "none"      # "live" | "upcoming" | "none"
    started_at: str | None = None       # ISO 8601 (actualStartTime)
    ended_at: str | None = None         # ISO 8601 (actualEndTime)
    thumbnail_url: str | None = None
    embeddable: bool = True
    concurrent_viewers: int | None = None

    @property
    def youtube_url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"

    @property
    def is_live(self) -> bool:
        return self.broadcast_status == "live" and not self.ended_at


@dataclass
class MatchResult:
    matched: bool
    score: int
    reasons: list[str]
