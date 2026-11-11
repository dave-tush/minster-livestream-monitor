from datetime import date

from fastapi.testclient import TestClient

from app.config import Settings
from app.database import Database
from app.main import create_app
from app.radio import RadioEngine


class NoopSource:
    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def fetch_since(self, min_id):
        return [], min_id

    async def download(self, message_id, dest):
        raise AssertionError("download is not part of this test")


def test_catalog_admin_config_and_stop(tmp_path):
    db = Database(f"sqlite:///{tmp_path}/radio.db")
    db.init_schema()
    db.radio_add_tracks([
        {"message_id": 1, "title": "Consecration in the Spirit", "duration": 60},
        {"message_id": 2, "title": "A message on Faith", "duration": 75},
    ], 2)
    settings = Settings(minister_name="", youtube_api_key="", admin_api_key="secret",
                        radio_cache_dir=str(tmp_path / "cache"))
    engine = RadioEngine(settings, db, NoopSource())
    app = create_app(settings=settings, background=False, radio=engine)
    with TestClient(app) as client:
        assert client.get("/radio/catalog", params={"search": "consecration"}).json()["total"] == 1
        assert client.get("/radio/admin/config").status_code == 401
        headers = {"X-API-Key": "secret"}
        saved = client.post("/radio/admin/config", headers=headers, json={
            "enabled": True, "season_name": "Consecration", "emphasis": "Consecration",
            "starts_on": date.today().isoformat(), "ends_on": date.today().isoformat(), "track_ids": [1],
        })
        assert saved.status_code == 200, saved.text
        assert saved.json()["season_track_ids"] == [1]
        assert client.get("/radio/now").json()["season"]["name"] == "Consecration"
        stopped = client.post("/radio/admin/config", headers=headers, json={
            "enabled": False, "season_name": "Consecration", "emphasis": "Consecration",
            "starts_on": date.today().isoformat(), "ends_on": date.today().isoformat(), "track_ids": [1],
        })
        assert stopped.status_code == 200
        response = client.get("/radio/now")
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "stopped"
