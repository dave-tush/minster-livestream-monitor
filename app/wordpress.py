"""Stage 5: client for the WordPress plugin's REST endpoint.

Contract (implemented by the plugin in Stage 6):
  POST {WORDPRESS_URL}/wp-json/minister-live/v1/status
       header  X-Minister-Live-Key: <WORDPRESS_API_KEY>
       body    {"status": "live", "video_id": ..., "title": ..., ...}   or   {"status": "offline"}
       reply   200 {"success": true, "status": "live" | "offline"}
  GET  same URL, no auth -> {"status": ...}  (public, what the shortcode polls)

The key is sent in a custom header (some hosts strip the standard Authorization header) and is
never logged or included in error messages.

Manual test without the server:
  python -m app.wordpress ping
  python -m app.wordpress live VIDEO_ID ["Title"]
  python -m app.wordpress offline
"""
import logging
import sys
from urllib.parse import urlparse

import httpx

from .config import Settings

log = logging.getLogger("monitor.wordpress")

ROUTE = "/minister-live/v1/status"
KEY_HEADER = "X-Minister-Live-Key"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class WordPressError(Exception):
    """Any failure talking to WordPress (never contains the API key)."""


class WordPressAuthError(WordPressError):
    """WordPress rejected our API key."""


def validate_base_url(url: str) -> str:
    parsed = urlparse(url or "")
    if parsed.scheme == "https" and parsed.hostname:
        return url.rstrip("/")
    if parsed.scheme == "http" and parsed.hostname in LOCAL_HOSTS:
        return url.rstrip("/")          # plain http only for local testing
    raise ValueError("WORDPRESS_URL must be an https:// address (http:// is only allowed for localhost)")


class WordPressClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 15.0,
                 client: httpx.Client | None = None):
        if not api_key:
            raise ValueError("WORDPRESS_API_KEY is empty")
        self.base_url = validate_base_url(base_url)
        self._key = api_key
        self._http = client or httpx.Client(timeout=timeout)   # redirects are NOT followed

    # ---- public API ------------------------------------------------------
    def push_live(self, stream: dict[str, str | None]) -> None:
        payload: dict[str, str | None] = {"status": "live"}
        for field in ("video_id", "title", "channel_title", "channel_id",
                      "youtube_url", "started_at", "thumbnail_url"):
            payload[field] = stream.get(field)
        self._expect_status(self._request("POST", payload), "live")

    def push_offline(self) -> None:
        self._expect_status(self._request("POST", {"status": "offline"}), "offline")

    def get_status(self) -> dict:
        return self._request("GET")

    # ---- internals -------------------------------------------------------
    @staticmethod
    def _expect_status(body: dict, expected: str) -> None:
        if body.get("status") != expected:
            raise WordPressError("Unexpected reply from WordPress (status not confirmed)")

    def _request(self, method: str, payload: dict[str, str | None] | None = None) -> dict:
        headers = {KEY_HEADER: self._key} if method == "POST" else {}
        urls = [f"{self.base_url}/wp-json{ROUTE}",
                f"{self.base_url}/?rest_route={ROUTE}"]    # fallback when pretty permalinks are off
        for index, url in enumerate(urls):
            try:
                resp = self._http.request(method, url, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                raise WordPressError(f"Network error contacting WordPress: {type(exc).__name__}") from None
            if resp.status_code == 404 and index == 0:
                continue
            return self._parse(resp)
        raise WordPressError("unreachable")   # pragma: no cover

    @staticmethod
    def _parse(resp: httpx.Response) -> dict:
        code = resp.status_code
        if 300 <= code < 400:
            raise WordPressError(f"WordPress redirected the request (HTTP {code}) to "
                                 f"{resp.headers.get('location', 'another address')}. "
                                 "Set WORDPRESS_URL to the final address (check www and https).")
        if code in (401, 403):
            raise WordPressAuthError(f"WordPress rejected the request (HTTP {code}). Check that "
                                     "WORDPRESS_API_KEY matches the plugin setting, and that no "
                                     "firewall/security plugin is blocking the endpoint.")
        if code == 404:
            raise WordPressError("WordPress endpoint not found (HTTP 404). Is the Minister Live "
                                 "plugin installed and activated?")
        if not 200 <= code < 300:
            raise WordPressError(f"WordPress returned HTTP {code}")
        try:
            body = resp.json()
        except ValueError:
            raise WordPressError("WordPress reply was not JSON (maintenance page or firewall?)") from None
        if not isinstance(body, dict):
            raise WordPressError("Unexpected reply shape from WordPress")
        return body


if __name__ == "__main__":
    logging.getLogger("httpx").setLevel(logging.WARNING)
    args = sys.argv[1:]
    settings = Settings.from_env()
    if not (settings.wordpress_url and settings.wordpress_api_key):
        sys.exit("Set WORDPRESS_URL and WORDPRESS_API_KEY in .env first.")
    wp = WordPressClient(settings.wordpress_url, settings.wordpress_api_key)
    try:
        if args[:1] == ["ping"]:
            print("WordPress says:", wp.get_status())
        elif args[:1] == ["live"] and len(args) >= 2:
            vid = args[1]
            wp.push_live({"video_id": vid, "title": args[2] if len(args) > 2 else "Test Livestream",
                          "channel_title": "Test Channel", "channel_id": "UCtest",
                          "youtube_url": f"https://www.youtube.com/watch?v={vid}",
                          "started_at": "2026-10-07T08:00:00Z"})
            print("Pushed LIVE to WordPress. Check your page.")
        elif args[:1] == ["offline"]:
            wp.push_offline()
            print("Pushed OFFLINE to WordPress.")
        else:
            sys.exit("Usage: python -m app.wordpress ping | live VIDEO_ID [TITLE] | offline")
    except WordPressError as exc:
        sys.exit(f"WordPress error: {exc}")