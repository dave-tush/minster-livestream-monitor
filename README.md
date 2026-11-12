# minster-livestream-monitor

# testing live server

## Render smoke test

Create a Render **Web Service** for this repository and use `python run.py` as the
start command. The service listens on Render's assigned `PORT`. Once deployed,
open the service's base URL to see the running status page, or visit `/health`
for a JSON health check.

The web service can start without monitoring credentials for deployment checks.
To enable livestream monitoring, configure `MINISTER_NAME` and
`YOUTUBE_API_KEY` in the Render environment variables. See `.env.example` for
the other optional settings.

## Dominion Radio — single process and crash recovery

Dominion Radio (the 24/7 station built from Telegram audio posts) keeps the
on-air pointer in `radio_state` (SQLite) and is designed to run as a **single
process with a single Uvicorn worker** against one SQLite file, which is how
the Render deployment works. The engine serialises all on-air pointer,
checkpoint and track-transition writes with one in-process lock and relies on
SQLite for atomic state persistence; there is no cross-process locking, so do
not run multiple workers of this service against the same database file.

Playback position is checkpointed to `radio_state` (`position_seconds`,
`checkpoint_at`) roughly every five seconds. Positions are persisted with
sub-second fractional precision (REAL values stored in the existing column;
integer positions from older databases restore unchanged), and server downtime
does not advance playback. After a restart the station resumes the saved
track at the last checkpoint. The `/radio/now` response is unchanged —
existing WordPress and backend players keep working without changes. If you
ever point the app at an existing database, `position_seconds` /
`checkpoint_at` are added by an idempotent migration on startup. Rows saved
before checkpointing existed have `checkpoint_at = NULL`; on first playback
access they are bootstrapped deterministically: the saved track restarts at
position 0 with `started_at` and `checkpoint_at` set to the current time, and
the true pre-upgrade playback position cannot be recovered. After
bootstrapping, subsequent calls resume normally and server downtime is
excluded from playback progression.
## Staying awake and resuming on Render's free plan

* The service pings its own `/health` every 4 minutes (`app/keepalive.py`) and a watchdog restarts the
  radio/monitor loops if one dies or hangs. `GET /health/detail` shows both.
* A self-ping cannot wake a service that is already asleep. Add a free external pinger
  (UptimeRobot or cron-job.org) on `https://<your-service>.onrender.com/health` every 5 minutes.
* Render's free disk is wiped on every restart, so the on-air position is also saved to one message in
  the Telegram account's Saved Messages (`RADIO_SNAPSHOT`) and restored on startup.
* When a listener's file finishes, the player sends `GET /radio/now?ended=<id>` so the station moves to
  the next sermon even if Telegram's reported length was a little off. Real lengths are read from each
  file after download (needs `mutagen`).
