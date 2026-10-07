import pytest

from app.database import Database
from app.models import LiveVideo


@pytest.fixture
def db(tmp_path):
    database = Database(f"sqlite:///{tmp_path}/test.db")
    database.init_schema()
    return database


def video(video_id="ABC123", title="Sunday Service"):
    return LiveVideo(video_id=video_id, title=title, channel_id="UC1", channel_title="Example Ministry",
                     broadcast_status="live", started_at="2026-10-06T08:00:00Z")


def test_starts_offline(db):
    assert db.get_current() is None


def test_record_live_sets_current(db):
    row, is_new = db.record_live(video(), "Pastor X", 90, ["name in title"])
    assert is_new and row["status"] == "live"
    assert db.get_current()["video_id"] == "ABC123"


def test_no_duplicates(db):
    db.record_live(video(), "Pastor X")
    _, is_new = db.record_live(video(title="Renamed"), "Pastor X")
    assert not is_new
    assert len(db.list_recent()) == 1
    assert db.get_livestream("ABC123")["title"] == "Renamed"


def test_mark_ended(db):
    db.record_live(video(), "Pastor X")
    assert db.mark_ended("ABC123") is True
    assert db.get_current() is None
    assert db.get_livestream("ABC123")["status"] == "ended"
    assert db.mark_ended("ABC123") is False   # already ended


def test_ending_old_stream_keeps_new_current(db):
    db.record_live(video("OLD"), "Pastor X")
    db.record_live(video("NEW"), "Pastor X")
    db.mark_ended("OLD")
    assert db.get_current()["video_id"] == "NEW"


def test_rejects_non_sqlite_url():
    with pytest.raises(ValueError):
        Database("postgresql://x")
