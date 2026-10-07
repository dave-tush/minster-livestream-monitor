"""Stage 1: YouTube Data API v3 client + live detection (official API only, no scraping).

Quota costs (default quota is 10,000 units/day):
  search.list        100 units   -> used sparingly (discovery)
  playlistItems.list   1 unit    -> cheap way to see a channel's newest videos
  videos.list          1 unit    -> verify live/ended status for up to 50 IDs
"""
import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from .config import Settings
from .matching import evaluate
from .models import LiveVideo, MatchResult

log = logging.getLogger("monitor.youtube")

API_BASE = "https://www.googleapis.com/youtube/v3"
PACIFIC = ZoneInfo("America/Los_Angeles")   # YouTube quota resets at midnight Pacific
QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"}


class YouTubeError(Exception):
    """Any failure talking to the YouTube API (never contains the API key)."""


class YouTubeQuotaError(YouTubeError):
    """Daily quota exhausted; retrying immediately is pointless."""


class YouTubeClient:
    def __init__(self, api_key: str, timeout: float = 15.0, client: httpx.Client | None = None):
        self._key = api_key
        self._http = client or httpx.Client(timeout=timeout)
        self.units_today = 0            # estimated quota spent since midnight Pacific
        self._quota_day: str | None = None

    def _spend(self, units: int) -> None:
        today = datetime.now(PACIFIC).strftime("%Y-%m-%d")
        if today != self._quota_day:
            self._quota_day, self.units_today = today, 0
        self.units_today += units

    def _get(self, endpoint: str, _cost: int = 1, **params) -> dict:
        self._spend(_cost)
        params["key"] = self._key
        try:
            resp = self._http.get(f"{API_BASE}/{endpoint}", params=params)
        except httpx.HTTPError as exc:
            # Don't chain the original exception: its message can contain the request URL (and key).
            raise YouTubeError(f"Network error calling {endpoint}: {type(exc).__name__}") from None

        try:
            body = resp.json()
        except ValueError:
            raise YouTubeError(f"{endpoint}: non-JSON response (HTTP {resp.status_code})") from None

        if resp.status_code != 200:
            err = body.get("error") if isinstance(body, dict) else None
            err = err if isinstance(err, dict) else {}
            errors = err.get("errors") or [{}]
            reason = errors[0].get("reason", "unknown")
            detail = str(err.get("message", ""))[:200]   # Google's message never contains the key
            msg = f"{endpoint}: HTTP {resp.status_code} ({reason}) {detail}".strip()
            raise (YouTubeQuotaError if reason in QUOTA_REASONS else YouTubeError)(msg)
        if not isinstance(body, dict):
            raise YouTubeError(f"{endpoint}: unexpected response shape")
        return body

    # ---- discovery -------------------------------------------------------
    def search_live_video_ids(self, query: str, max_results: int = 10) -> list[str]:
        """100 quota units. Finds currently-live videos matching the query."""
        data = self._get("search", part="snippet", type="video", eventType="live",
                         q=query, maxResults=max_results, order="relevance", _cost=100)
        return [i["id"]["videoId"] for i in data.get("items", [])
                if isinstance(i.get("id"), dict) and i["id"].get("videoId")]

    def channel_recent_video_ids(self, channel_id: str, max_results: int = 10) -> list[str]:
        """1 quota unit. A channel's uploads playlist ID is its channel ID with 'UC' -> 'UU'."""
        if not channel_id.startswith("UC"):
            raise YouTubeError(f"Invalid channel id: {channel_id!r}")
        data = self._get("playlistItems", part="contentDetails",
                         playlistId="UU" + channel_id[2:], maxResults=max_results)
        return [i["contentDetails"]["videoId"] for i in data.get("items", [])
                if (i.get("contentDetails") or {}).get("videoId")]

    # ---- verification ----------------------------------------------------
    def fetch_videos(self, video_ids: list[str]) -> list[LiveVideo]:
        """1 quota unit per call (up to 50 IDs). Returns parsed videos in API order."""
        videos: list[LiveVideo] = []
        for start in range(0, len(video_ids), 50):
            chunk = video_ids[start:start + 50]
            data = self._get("videos", part="snippet,liveStreamingDetails,status",
                             id=",".join(chunk), maxResults=50)
            for item in data.get("items", []):
                video = parse_video(item)
                if video:
                    videos.append(video)
        return videos

    def is_still_live(self, video_id: str) -> bool:
        """1 quota unit. Used to confirm the stream we're tracking hasn't ended."""
        videos = self.fetch_videos([video_id])
        return bool(videos and videos[0].is_live)


def parse_video(item: dict) -> LiveVideo | None:
    """Convert one videos.list item into a LiveVideo. Returns None if malformed."""
    try:
        video_id = item["id"]
        snippet = item["snippet"]
    except (KeyError, TypeError):
        return None
    if not isinstance(video_id, str) or not isinstance(snippet, dict):
        return None

    live = item.get("liveStreamingDetails") or {}
    thumbs = snippet.get("thumbnails") or {}
    thumb = next((thumbs[k]["url"] for k in ("maxres", "standard", "high", "medium", "default")
                  if k in thumbs and "url" in thumbs[k]), None)

    try:
        viewers = int(live["concurrentViewers"])
    except (KeyError, TypeError, ValueError):
        viewers = None

    return LiveVideo(
        video_id=video_id,
        title=snippet.get("title", ""),
        channel_id=snippet.get("channelId", ""),
        channel_title=snippet.get("channelTitle", ""),
        description=snippet.get("description", ""),
        broadcast_status=snippet.get("liveBroadcastContent", "none"),
        started_at=live.get("actualStartTime"),
        ended_at=live.get("actualEndTime"),
        thumbnail_url=thumb,
        embeddable=(item.get("status") or {}).get("embeddable", True),
        concurrent_viewers=viewers,
    )


def find_live_streams(client: YouTubeClient, settings: Settings,
                      run_search: bool = True) -> list[tuple[LiveVideo, MatchResult]]:
    """Return matching live videos, best match first.

    Cheap path: trusted channels' recent uploads (1 unit each).
    Expensive path (run_search=True): keyword search (100 units). Skipped if trusted_only.
    One failing channel does not stop the others; quota errors always propagate.
    """
    candidate_ids: list[str] = []

    for channel_id in settings.trusted_channel_ids:
        try:
            candidate_ids += client.channel_recent_video_ids(channel_id)
        except YouTubeQuotaError:
            raise
        except YouTubeError as exc:
            log.warning("Could not read channel %s: %s", channel_id, exc)

    if run_search and not settings.trusted_only:
        candidate_ids += client.search_live_video_ids(settings.minister_name)

    unique_ids = list(dict.fromkeys(candidate_ids))
    if not unique_ids:
        return []

    results = []
    for video in client.fetch_videos(unique_ids):
        if not video.is_live:
            continue
        match = evaluate(video, settings)
        if not match.matched:
            log.info("Ignored live video %s (%r on %r): not a confident match (score %s)",
                     video.video_id, video.title, video.channel_title, match.score)
            continue
        if not video.embeddable:
            log.warning("Live video %s matched but embedding is disabled by the owner", video.video_id)
            continue
        results.append((video, match))

    # Best score first; ties go to the stream with the most live viewers (usually the original).
    results.sort(key=lambda pair: (pair[1].score, pair[0].concurrent_viewers or 0), reverse=True)
    return results


def diagnose(client: YouTubeClient, settings: Settings) -> None:
    """Print every candidate YouTube returns and why it is accepted or rejected."""
    ids: list[str] = []
    for channel_id in settings.trusted_channel_ids:
        ids += client.channel_recent_video_ids(channel_id)
    ids += client.search_live_video_ids(settings.minister_name)
    ids = list(dict.fromkeys(ids))

    print(f"Searching for: {settings.minister_name!r}  (trusted channels: {len(settings.trusted_channel_ids)})")
    if not ids:
        print("YouTube returned NO live videos for this search. The stream may not be indexed as "
              "live yet, or the name doesn't match how YouTube lists it.")
        return
    print(f"YouTube returned {len(ids)} candidate(s):\n")
    for video in client.fetch_videos(ids):
        match = evaluate(video, settings)
        if not video.is_live:
            verdict = f"SKIP    not live (status: {video.broadcast_status})"
        elif not match.matched:
            verdict = f"REJECT  score {match.score} < 40 ({', '.join(match.reasons) or 'no name match'})"
        elif not video.embeddable:
            verdict = "SKIP    matched, but the owner disabled embedding"
        else:
            verdict = f"ACCEPT  score {match.score} ({', '.join(match.reasons)})"
        print(f"{verdict}\n   title:   {video.title}\n   channel: {video.channel_title} "
              f"[{video.channel_id}]\n   viewers: {video.concurrent_viewers}\n   id:      {video.video_id}\n")


if __name__ == "__main__":
    # Manual test:  python -m app.youtube             (with search)
    #               python -m app.youtube --debug     (show every candidate and why)
    #               python -m app.youtube --no-search (trusted channels only)
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)   # httpx logs full URLs, including the API key
    settings = Settings.from_env()
    settings.require_youtube()
    if not settings.youtube_api_key.startswith("AIza"):
        print("Warning: YouTube API keys normally start with 'AIza'. Check YOUTUBE_API_KEY in .env.")
    try:
        if "--debug" in sys.argv:
            diagnose(YouTubeClient(settings.youtube_api_key), settings)
            sys.exit(0)
        found = find_live_streams(YouTubeClient(settings.youtube_api_key), settings,
                                  run_search="--no-search" not in sys.argv)
    except YouTubeError as exc:
        print(f"YouTube API error: {exc}")
        sys.exit(1)
    if not found:
        print("No matching livestream found.")
    for video, match in found:
        print(f"LIVE  {video.video_id}  {video.title!r}  [{video.channel_title}]  "
              f"score={match.score} viewers={video.concurrent_viewers} ({', '.join(match.reasons)})")


