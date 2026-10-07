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


def test_starts_without_monitoring_credentials():
    settings = Settings(minister_name="", youtube_api_key="")
    app = create_app(settings, background=False)
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/status").json() == {"status": "offline"}


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
