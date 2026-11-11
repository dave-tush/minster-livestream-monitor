import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.database import Database
from app.main import create_app
from app.models import LiveVideo
from app.monitor import Monitor


class FakeYouTube:
    """Stands in for YouTubeClient so no network/quota is used."""
    def __init__(self):
        self.videos: dict[str, LiveVideo] = {}
        self.units_today = 0

    def channel_recent_video_ids(self, channel_id, max_results=10):
        return list(self.videos)

    def search_live_video_ids(self, query, max_results=10):
        return list(self.videos)

    def fetch_videos(self, ids):
        return [self.videos[i] for i in ids if i in self.videos]

    def is_still_live(self, video_id):
        v = self.videos.get(video_id)
        return bool(v and v.is_live)


def live_video(video_id="ABC123", status="live"):
    return LiveVideo(video_id=video_id, title="Sunday Service with Pastor Example", channel_id="UC1",
                     channel_title="Example Ministry", broadcast_status=status,
                     started_at="2026-10-06T08:00:00Z")


@pytest.fixture
def env(tmp_path):
    settings = Settings(minister_name="Pastor Example", youtube_api_key="k", admin_api_key="secret")
    db = Database(f"sqlite:///{tmp_path}/t.db")
    db.init_schema()
    yt = FakeYouTube()
    app = create_app(settings, Monitor(settings, db, yt))
    with TestClient(app) as client:
        yield client, yt, db


H = {"X-API-Key": "secret"}


def test_health(env):
    assert env[0].get("/health").json() == {"status": "ok"}


def test_root_reports_service_running(env):
    response = env[0].get("/")
    assert response.status_code == 200
    assert "Server is running" in response.text
    assert response.headers["content-type"].startswith("text/html")


def test_cors_allows_wordpress_origin(env):
    """CORS middleware must allow the WordPress origin for GET/OPTIONS."""
    client, _, _ = env
    response = client.options(
        "/radio/now",
        headers={
            "Origin": "https://toluwalogoagboola.org",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://toluwalogoagboola.org"
    assert "GET" in response.headers["access-control-allow-methods"]
    assert "OPTIONS" in response.headers["access-control-allow-methods"]


def test_cors_get_response_headers(env):
    """CORS headers must be present on actual GET responses, not just preflight."""
    client, _, _ = env
    response = client.get(
        "/radio/now",
        headers={"Origin": "https://toluwalogoagboola.org"},
    )
    assert response.status_code in (200, 503)  # 503 if radio not configured, but CORS headers still present
    assert response.headers["access-control-allow-origin"] == "https://toluwalogoagboola.org"


def test_cors_rejects_unapproved_origin(env):
    """CORS must not echo back or allow unapproved origins."""
    client, _, _ = env
    response = client.options(
        "/radio/now",
        headers={
            "Origin": "https://evil.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    # Unapproved origin: no ACAO header should be present
    assert "access-control-allow-origin" not in response.headers


def test_cors_allows_range_header(env):
    """CORS must allow the Range header for audio seeking."""
    client, _, _ = env
    response = client.options(
        "/radio/audio/1",
        headers={
            "Origin": "https://toluwalogoagboola.org",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Range",
        },
    )
    assert response.status_code == 200
    assert "range" in response.headers["access-control-allow-headers"].lower()


def test_cors_exposes_range_headers(env):
    """CORS must expose Content-Range and Accept-Ranges to the browser on GET responses."""
    client, _, _ = env
    response = client.get(
        "/radio/now",
        headers={"Origin": "https://toluwalogoagboola.org"},
    )
    assert response.status_code in (200, 503)  # 503 if radio not configured
    expose = response.headers.get("access-control-expose-headers", "")
    assert "content-range" in expose.lower()
    assert "accept-ranges" in expose.lower()


def test_public_endpoints_no_auth(env):
    """Public playback endpoints must work without any authentication headers."""
    client, _, _ = env
    # /radio/now — may return 503 if radio engine not configured, but must not return 401/403
    r = client.get("/radio/now")
    assert r.status_code in (200, 503)
    assert r.status_code not in (401, 403)

    # /radio/playlist — may return 503 if radio engine not configured, but must not return 401/403
    r = client.get("/radio/playlist")
    assert r.status_code in (200, 503)
    assert r.status_code not in (401, 403)


def test_sync_now_requires_admin_key(env):
    """POST /radio/sync-now must reject missing or invalid admin keys."""
    client, _, _ = env
    # No key at all
    r = client.post("/radio/sync-now")
    assert r.status_code == 401

    # Wrong key
    r = client.post("/radio/sync-now", headers={"X-API-Key": "wrong"})
    assert r.status_code == 401

    # Correct key — auth passes, but radio engine not configured in test env
    r = client.post("/radio/sync-now", headers={"X-API-Key": "secret"})
    assert r.status_code == 503  # Radio engine not configured, but auth passed (not 401/403)


def test_starts_without_monitoring_credentials():
    settings = Settings(minister_name="", youtube_api_key="")
    app = create_app(settings, background=False)
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/status").json() == {"status": "unconfigured"}


def test_status_offline(env):
    assert env[0].get("/status").json() == {"status": "offline"}


def test_check_now_requires_key(env):
    client, _, _ = env
    assert client.post("/check-now").status_code == 401
    assert client.post("/check-now", headers={"X-API-Key": "wrong"}).status_code == 401


def test_check_now_disabled_without_admin_key(tmp_path):
    settings = Settings(minister_name="P", youtube_api_key="k")
    db = Database(f"sqlite:///{tmp_path}/t.db"); db.init_schema()
    app = create_app(settings, Monitor(settings, db, FakeYouTube()))
    with TestClient(app) as client:
        assert client.post("/check-now", headers=H).status_code == 503


def test_detects_live_then_ended(env):
    client, yt, db = env
    yt.videos["ABC123"] = live_video()

    r = client.post("/check-now", headers=H).json()
    assert r["status"] == "live" and r["video_id"] == "ABC123"
    s = client.get("/status").json()
    assert s["status"] == "live" and s["youtube_url"].endswith("ABC123")

    # same stream again: still live, no duplicate row
    assert client.post("/check-now", headers=H).json()["action"] == "still_live"
    assert len(db.list_recent()) == 1

    # stream ends
    yt.videos["ABC123"] = live_video(status="none")
    yt.videos["ABC123"].ended_at = "2026-10-06T10:00:00Z"
    assert client.post("/check-now", headers=H).json()["status"] == "offline"
    assert client.get("/status").json() == {"status": "offline"}


def test_unrelated_live_video_is_ignored(env):
    client, yt, _ = env
    v = live_video()
    v.title, v.channel_title = "Gaming stream", "Some Gamer"
    yt.videos["ABC123"] = v
    assert client.post("/check-now", headers=H).json()["status"] == "offline"
