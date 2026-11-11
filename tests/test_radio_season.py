from datetime import date, datetime, timedelta, timezone

from app.config import Settings
from app.database import Database
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


def make_db(tmp_path):
    db = Database(f"sqlite:///{tmp_path}/radio.db")
    db.init_schema()
    db.radio_add_tracks([
        {"message_id": 1, "title": "Consecration — prayer", "duration": 30},
        {"message_id": 2, "title": "Faith message", "duration": 40},
    ], 2)
    return db


def test_title_catalog_and_active_season_select_only_chosen_sermons(tmp_path):
    db = make_db(tmp_path)
    rows, total = db.radio_catalog("consecration", 20, 0)
    assert total == 1
    assert rows[0]["title"] == "Consecration — prayer"

    today = date.today().isoformat()
    db.radio_settings_set(enabled=True, season_name="Prayer Season", emphasis="Consecration",
                          starts_on=today, ends_on=today, track_ids=[2])
    assert db.radio_settings_get()["season_active"]
    assert db.radio_count() == 1
    assert db.radio_first_track()["message_id"] == 2


def test_single_track_rotation_advances_its_started_at(tmp_path):
    db = make_db(tmp_path)
    today = date.today().isoformat()
    db.radio_settings_set(enabled=True, season_name="One Sermon", emphasis="Test",
                          starts_on=today, ends_on=today, track_ids=[1])
    start = datetime.now(timezone.utc).replace(microsecond=0)
    mono = [0.0]
    settings = Settings(minister_name="", youtube_api_key="", radio_cache_dir=str(tmp_path / "cache"))
    engine = RadioEngine(settings, db, NoopSource(), clock=lambda: start, mono_clock=lambda: mono[0])
    first = engine.now_playing(start)
    mono[0] = 31.0
    second = engine.now_playing(start + timedelta(seconds=31))
    assert first["track"]["id"] == second["track"]["id"] == 1
    assert second["started_at"] != first["started_at"]
    assert second["position"] == 1
    assert second["season"]["emphasis"] == "Test"


def test_season_rotation_replaces_an_out_of_season_track(tmp_path):
    db = make_db(tmp_path)
    today = date.today().isoformat()
    db.radio_settings_set(enabled=True, season_name="Season", emphasis="Emphasis",
                          starts_on=today, ends_on=today, track_ids=[2])
    start = datetime.now(timezone.utc).replace(microsecond=0)
    db.radio_set_state(1, (start - timedelta(seconds=20)).strftime("%Y-%m-%dT%H:%M:%SZ"), 20,
                       (start - timedelta(seconds=20)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    settings = Settings(minister_name="", youtube_api_key="", radio_cache_dir=str(tmp_path / "cache"))
    mono = [0.0]
    engine = RadioEngine(settings, db, NoopSource(), clock=lambda: start, mono_clock=lambda: mono[0])
    assert engine.now_playing(start)["track"]["id"] == 2


def test_admin_stop_makes_station_off_air(tmp_path):
    db = make_db(tmp_path)
    db.radio_settings_set(enabled=False, season_name="", emphasis="", starts_on=None, ends_on=None, track_ids=[])
    settings = Settings(minister_name="", youtube_api_key="", radio_cache_dir=str(tmp_path / "cache"))
    engine = RadioEngine(settings, db, NoopSource())
    assert engine.now_playing() is None
