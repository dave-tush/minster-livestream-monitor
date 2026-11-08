#!/usr/bin/env python3
"""Local preview server: static files + a MOCK of the Dominion Radio /radio/now and
/radio/audio/{id} contract (same JSON shape the real plugin JS reads). Nothing here
touches the real backend. Run:  python3 serve.py   then open http://127.0.0.1:8765/"""
import json, os, re, time
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
START = time.time()
DUR = 312
TRACKS = [
  {"id": 101, "title": "Great Is Thy Faithfulness", "performer": "Dominion Worship Choir"},
  {"id": 102, "title": "Abide With Me", "performer": "Ibadan Gospel Voices"},
]

def now_payload():
    pos = (138 + (time.time() - START)) % DUR
    cur = TRACKS[0]
    iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat()
    return {"status": "live", "position": pos, "started_at": iso(time.time() - pos), "server_time": iso(time.time()),
            "track": {**cur, "duration": DUR, "audio_url": f"/radio/audio/{cur['id']}"},
            "next": {**TRACKS[1], "duration": 281}}

class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k): super().__init__(*a, directory=ROOT, **k)
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path.startswith("/radio/now"):
            b = json.dumps(now_payload()).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b))); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(b); return
        if self.path.startswith("/radio/audio/"):
            data = open(os.path.join(ROOT, "assets", "mock.wav"), "rb").read(); n = len(data)
            m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
            if m:
                a = int(m.group(1) or 0); z = int(m.group(2)) if m.group(2) else n - 1
                z = min(z, n - 1); chunk = data[a:z + 1]
                self.send_response(206); self.send_header("Content-Range", f"bytes {a}-{z}/{n}")
            else:
                chunk = data; self.send_response(200)
            self.send_header("Content-Type", "audio/wav"); self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(len(chunk))); self.end_headers(); self.wfile.write(chunk); return
        super().do_GET()

if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8765), H).serve_forever()
