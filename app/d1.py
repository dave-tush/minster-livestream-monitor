"""Cloudflare D1 as durable storage for the on-air position.

Render's free disk is wiped on every restart; D1 lives outside Render, so "which sermon, how far in"
survives. Talks to D1's HTTP API with httpx (no extra dependency). One tiny row, written about once
a minute, so it stays far inside D1's free plan (100,000 row writes per day).
"""
import logging

import httpx

log = logging.getLogger("d1")

API = "https://api.cloudflare.com/client/v4/accounts/{account}/d1/database/{database}/query"

CREATE = ("CREATE TABLE IF NOT EXISTS radio_state_backup ("
          "id INTEGER PRIMARY KEY CHECK (id = 1), channel TEXT NOT NULL, message_id INTEGER NOT NULL, "
          "position REAL NOT NULL, saved_at TEXT NOT NULL)")
UPSERT = ("INSERT INTO radio_state_backup (id, channel, message_id, position, saved_at) VALUES (1, ?, ?, ?, ?) "
          "ON CONFLICT(id) DO UPDATE SET channel = excluded.channel, message_id = excluded.message_id, "
          "position = excluded.position, saved_at = excluded.saved_at")


class D1Error(RuntimeError):
    pass


class D1StateStore:
    def __init__(self, account_id: str, database_id: str, api_token: str, timeout: float = 15):
        self._url = API.format(account=account_id, database=database_id)
        self._headers = {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}
        self._timeout = timeout
        self._ready = False

    async def _query(self, sql: str, params: list | None = None) -> list[dict]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.post(self._url, headers=self._headers, json={"sql": sql, "params": params or []})
        try:
            body = r.json()
        except ValueError:
            raise D1Error(f"D1 returned HTTP {r.status_code} with no JSON") from None
        if r.status_code != 200 or not body.get("success"):
            errs = "; ".join(str(e.get("message", e)) for e in body.get("errors", [])) or f"HTTP {r.status_code}"
            raise D1Error(errs)
        result = body.get("result") or [{}]
        return result[0].get("results") or []

    async def _ensure_table(self) -> None:
        if not self._ready:
            await self._query(CREATE)
            self._ready = True

    async def save(self, data: dict) -> None:
        await self._ensure_table()
        await self._query(UPSERT, [data["channel"], int(data["message_id"]),
                                   float(data["position"]), data["saved_at"]])

    async def load(self) -> dict | None:
        await self._ensure_table()
        rows = await self._query("SELECT channel, message_id, position, saved_at "
                                 "FROM radio_state_backup WHERE id = 1")
        return rows[0] if rows else None
