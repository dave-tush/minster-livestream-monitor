"""Cloudflare D1 position storage (HTTP mocked with respx; nothing leaves the machine)."""
import asyncio
import json

import httpx
import respx

from app.d1 import API, D1Error, D1StateStore
from tests.test_radio_recovery import make_engine
from tests.test_radio_resilience import meta

URL = API.format(account="acc", database="db")


def ok(rows=None):
    return httpx.Response(200, json={"success": True, "errors": [],
                                     "result": [{"results": rows or [], "success": True}]})


@respx.mock
def test_save_creates_table_then_upserts():
    route = respx.post(URL).mock(return_value=ok())
    store = D1StateStore("acc", "db", "tok")
    asyncio.run(store.save({"channel": "c", "message_id": 5, "position": 12.5, "saved_at": "t"}))
    sqls = [json.loads(c.request.content)["sql"] for c in route.calls]
    assert sqls[0].startswith("CREATE TABLE") and sqls[1].startswith("INSERT INTO radio_state_backup")
    assert route.calls[0].request.headers["authorization"] == "Bearer tok"


@respx.mock
def test_load_returns_row_or_none():
    row = {"channel": "c", "message_id": 5, "position": 9.0, "saved_at": "t"}
    respx.post(URL).mock(side_effect=[ok(), ok([row])])
    assert asyncio.run(D1StateStore("acc", "db", "tok").load()) == row


@respx.mock
def test_api_error_raises_d1error():
    respx.post(URL).mock(return_value=httpx.Response(403, json={"success": False,
                         "errors": [{"message": "Authentication error"}]}))
    try:
        asyncio.run(D1StateStore("acc", "db", "bad").load())
        assert False
    except D1Error as e:
        assert "Authentication" in str(e)


class MemStore:
    def __init__(self): self.row = None
    async def save(self, d): self.row = d
    async def load(self): return self.row


class BrokenStore:
    async def save(self, d): raise D1Error("down")
    async def load(self): raise D1Error("down")


class Src:
    async def fetch_since(self, min_id, limit=None): return [], min_id
    async def fetch_track(self, mid): return meta(mid, 3600)


def test_resume_from_d1_after_render_wipes_disk(tmp_path):
    store = MemStore()
    eng, db, _ = make_engine(f"sqlite:///{tmp_path}/a.db")
    eng.source, eng.state_store = Src(), store
    db.radio_add_tracks([meta(i, 3600).as_row() for i in (1, 2, 3)], 3)
    db.radio_set_state(2, "2026-01-01T00:00:00Z", position_seconds=900, checkpoint_at="2026-01-01T00:00:00Z")
    eng._init_checkpoint_state()
    asyncio.run(eng.save_snapshot())
    assert store.row["message_id"] == 2

    eng2, _, _ = make_engine(f"sqlite:///{tmp_path}/wiped.db")      # fresh disk
    eng2.source, eng2.state_store = Src(), store
    assert asyncio.run(eng2.restore_from_snapshot()) is True
    now = eng2.now_playing()
    assert now["track"]["id"] == 2 and 899 <= now["position"] <= 902


def test_d1_outage_never_breaks_the_radio(tmp_path):
    eng, db, _ = make_engine(f"sqlite:///{tmp_path}/b.db")
    eng.source, eng.state_store = Src(), BrokenStore()
    db.radio_add_tracks([meta(1, 3600).as_row()], 1)
    asyncio.run(eng.save_snapshot())                  # must not raise
    assert asyncio.run(eng.restore_from_snapshot()) is False
