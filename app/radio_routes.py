"""HTTP endpoints for Dominion Radio.

  GET  /radio                 listener page (press "Listen live")
  GET  /radio/now             what is on air + exact position, as JSON
  GET  /radio/playlist        the rotation, oldest upload first (?limit=&offset=)
  GET  /radio/audio/{id}      the audio file (supports Range requests so players can seek)
  POST /radio/sync-now        admin only: pull new uploads from Telegram immediately
"""
import html
import secrets

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

PLAYER_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__NAME__</title>
<style>
  :root { color-scheme: dark; font-family: system-ui, sans-serif; }
  body { background: #0f1420; color: #e8ecf5; margin: 0; min-height: 100vh; display: flex;
         align-items: center; justify-content: center; }
  main { width: calc(100% - 40px); max-width: 440px; text-align: center; padding: 32px 0; }
  .live { display: inline-block; background: #3a1620; color: #ff6b81; font-size: 12px; font-weight: 700;
          letter-spacing: .12em; padding: 5px 12px; border-radius: 999px; }
  .live::before { content: ""; display: inline-block; width: 7px; height: 7px; margin-right: 7px;
                  border-radius: 50%; background: #ff4d6a; }
  h1 { font-size: 26px; margin: 18px 0 4px; }
  .sub { color: #8a95ad; font-size: 14px; min-height: 20px; }
  #title { font-size: 20px; font-weight: 650; margin: 30px 0 4px; word-break: break-word; }
  #performer { color: #8a95ad; min-height: 22px; }
  .bar { background: #222a3d; border-radius: 99px; height: 6px; margin: 22px 0 8px; overflow: hidden; }
  #fill { background: #5b8cff; height: 100%; width: 0; transition: width .5s linear; }
  .times { display: flex; justify-content: space-between; color: #8a95ad; font-size: 12px; }
  button { margin-top: 28px; background: #5b8cff; color: #fff; border: 0; border-radius: 999px;
           padding: 15px 38px; font-size: 17px; font-weight: 650; cursor: pointer; }
  button.stop { background: #2a3350; }
  #next { margin-top: 30px; color: #8a95ad; font-size: 13px; }
  #msg { color: #ffb347; font-size: 13px; min-height: 18px; margin-top: 14px; }
</style>
</head>
<body>
<main>
  <span class="live">LIVE</span>
  <h1>__NAME__</h1>
  <div class="sub" id="count"></div>
  <div id="title">Loading&hellip;</div>
  <div id="performer"></div>
  <div class="bar"><div id="fill"></div></div>
  <div class="times"><span id="pos">0:00</span><span id="dur">0:00</span></div>
  <button id="btn">&#9654; Listen live</button>
  <div id="msg"></div>
  <div id="next"></div>
  <audio id="audio" preload="auto"></audio>
</main>
<script>
const $ = id => document.getElementById(id);
const audio = $("audio");
let playing = false, currentId = null, now = null, fetchedAt = 0, switching = false;

const fmt = s => { s = Math.max(0, Math.floor(s)); return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0"); };
const msg = t => { $("msg").textContent = t || ""; };

async function getNow() {
  const t0 = performance.now();
  const r = await fetch("/radio/now", { cache: "no-store" });
  if (!r.ok) throw new Error("offline");
  const data = await r.json();
  const rtt = (performance.now() - t0) / 1000;
  data.position += rtt / 2;                      // compensate for network delay
  now = data; fetchedAt = performance.now();
  return data;
}

function livePosition() { return now ? now.position + (performance.now() - fetchedAt) / 1000 : 0; }

function render() {
  if (!now) return;
  $("title").textContent = now.track.title;
  $("performer").textContent = now.track.performer || "";
  $("dur").textContent = fmt(now.track.duration);
  $("next").textContent = now.next ? "Up next: " + now.next.title : "";
  $("count").textContent = now.tracks_in_rotation + " tracks in rotation";
  document.title = now.track.title + " \\u2022 " + now.station;
  if ("mediaSession" in navigator)
    navigator.mediaSession.metadata = new MediaMetadata({ title: now.track.title,
      artist: now.track.performer || now.station, album: now.station });
}

async function tune() {                          // load whatever is on air and jump to its position
  if (switching) return;
  switching = true;
  try {
    const d = await getNow();
    render();
    currentId = d.track.id;
    audio.src = d.track.audio_url;
    await new Promise((res, rej) => {
      audio.onloadedmetadata = res; audio.onerror = () => rej(new Error("load"));
    });
    audio.currentTime = Math.min(livePosition(), Math.max(0, (audio.duration || d.track.duration) - 0.5));
    await audio.play();
    msg("");
  } catch (e) {
    if (e.name === "NotAllowedError") { stopUi(); msg("Tap Listen live to start."); }
    else { msg("Reconnecting\\u2026"); setTimeout(() => { switching = false; if (playing) tune(); }, 3000); return; }
  }
  switching = false;
}

function stopUi() { playing = false; audio.pause(); $("btn").textContent = "\\u25B6 Listen live"; $("btn").className = ""; }

$("btn").onclick = () => {
  if (playing) { stopUi(); return; }
  playing = true; $("btn").textContent = "\\u25A0 Stop"; $("btn").className = "stop"; tune();
};
audio.onended = () => { if (playing) tune(); };

setInterval(async () => {                        // keep the display fresh and stay in sync with the station
  try {
    const d = await getNow();
    render();
    if (playing && !switching) {
      if (d.track.id !== currentId) tune();
      else if (Math.abs(audio.currentTime - livePosition()) > 5) audio.currentTime = livePosition();
    }
  } catch (e) { msg("Station unreachable\\u2026"); }
}, 15000);
setInterval(() => {
  if (!now) return;
  const p = Math.min(livePosition(), now.track.duration);
  $("pos").textContent = fmt(p);
  $("fill").style.width = (100 * p / now.track.duration) + "%";
}, 500);

getNow().then(render).catch(() => { $("title").textContent = "Station is warming up\\u2026"; });
</script>
</body>
</html>"""


def build_radio_router() -> APIRouter:
    router = APIRouter(prefix="/radio", tags=["radio"])

    def engine_or_503(request: Request):
        engine = request.app.state.radio
        if engine is None:
            raise HTTPException(503, "Radio is not configured (TELEGRAM_* settings)")
        return engine

    @router.api_route("", methods=["GET", "HEAD"], response_class=HTMLResponse)
    def player(request: Request):
        engine = engine_or_503(request)
        return PLAYER_PAGE.replace("__NAME__", html.escape(engine.settings.radio_name))

    @router.get("/now")
    def now(request: Request):
        engine = engine_or_503(request)
        playing = engine.now_playing()
        if playing is None:
            raise HTTPException(503, "No audio available yet. The station is still loading the channel.")
        return playing

    @router.get("/playlist")
    def playlist(request: Request, limit: int = 100, offset: int = 0):
        engine = engine_or_503(request)
        limit = max(1, min(limit, 500))
        rows = engine.db.radio_list_tracks(limit, max(0, offset))
        return {"total": engine.db.radio_count(),
                "total_seconds": engine.db.radio_total_seconds(),
                "tracks": [{"id": r["message_id"], "title": r["title"], "performer": r["performer"],
                            "duration": r["duration"], "posted_at": r["posted_at"]} for r in rows]}

    @router.get("/audio/{message_id}")
    async def audio(request: Request, message_id: int):
        engine = engine_or_503(request)
        path = await engine.ensure_cached(message_id)
        track = engine.db.radio_get_track(message_id)
        if path is None or track is None:
            raise HTTPException(404, "Audio not available")
        return FileResponse(path, media_type=track["mime_type"],
                            headers={"Cache-Control": "public, max-age=3600"})

    @router.post("/sync-now")
    async def sync_now(request: Request, x_api_key: str | None = Header(default=None)):
        expected = request.app.state.settings.admin_api_key
        if not expected:
            raise HTTPException(503, "Endpoint disabled: ADMIN_API_KEY is not configured")
        if not x_api_key or not secrets.compare_digest(x_api_key.encode(), expected.encode()):
            raise HTTPException(401, "Invalid API key")
        engine = engine_or_503(request)
        try:
            added = await engine.sync_once()
        except Exception:
            raise HTTPException(502, "Telegram sync failed; see server logs") from None
        return {"added": added, "tracks_in_rotation": engine.db.radio_count()}

    return router
