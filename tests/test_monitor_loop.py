import asyncio
from datetime import datetime

import pytest

import app.monitor as monitor_mod
from app.config import Settings
from app.database import Database
from app.monitor import Monitor, backoff_delay, seconds_until_quota_reset
from app.youtube import PACIFIC, YouTubeError, YouTubeQuotaError
from tests.test_api import FakeYouTube


@pytest.fixture
def mon(tmp_path):
    settings = Settings(minister_name="P", youtube_api_key="k", check_interval_seconds=0.01)
    db = Database(f"sqlite:///{tmp_path}/t.db")
    db.init_schema()
    return Monitor(settings, db, FakeYouTube())


def test_backoff_grows_and_is_capped():
    assert backoff_delay(60, 1) == 120
    assert backoff_delay(60, 3) == 480
    assert backoff_delay(60, 10) == 600


def test_quota_reset_is_within_a_day():
    now = datetime(2026, 10, 6, 23, 0, tzinfo=PACIFIC)
    assert seconds_until_quota_reset(now) == 3600 + 60


def run_loop(mon, outcomes):
    """Drive run_forever with scripted check_once outcomes; stop after the script is used up."""
    stop = asyncio.Event()
    calls = []

    def fake_check(force_search=False):
        calls.append(1)
        outcome = outcomes[len(calls) - 1]
        if len(calls) == len(outcomes):
            stop.set()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    mon.check_once = fake_check
    asyncio.run(asyncio.wait_for(mon.run_forever(stop), timeout=5))
    return len(calls)


def test_loop_survives_errors_and_keeps_going(mon):
    outcomes = [YouTubeError("boom"), RuntimeError("db down"), {"status": "offline"}, {"status": "offline"}]
    assert run_loop(mon, outcomes) == 4          # kept running through both failures


def test_loop_pauses_on_quota_then_resumes(mon, monkeypatch):
    monkeypatch.setattr(monitor_mod, "seconds_until_quota_reset", lambda: 0.01)
    assert run_loop(mon, [YouTubeQuotaError("quotaExceeded"), {"status": "offline"}]) == 2


def test_search_skipped_when_quota_low(mon):
    assert mon._search_due() is True
    mon.youtube.units_today = 9000      # 9000 + 100 > 10000 - 1500
    assert mon._search_due() is False
