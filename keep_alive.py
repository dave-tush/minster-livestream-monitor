#!/usr/bin/env python3
"""Ping the Dominion Radio backend every few minutes so Render's free tier does not put it to sleep.

Render spins a free web service down after 15 minutes without inbound traffic, so the default
interval is 10 minutes. Uses only the Python standard library.

    python scripts/keep_alive.py                       # runs forever, pings every 10 minutes
    python scripts/keep_alive.py --once                # one ping, then exit (good for cron)
    python scripts/keep_alive.py --url https://my-service.onrender.com/health --interval 480

Settings can also come from the environment: KEEP_ALIVE_URL, KEEP_ALIVE_INTERVAL (seconds).

NOTE: the backend now pings itself too (see app/keepalive.py). This script is only the outside
backup that can wake a service that has already gone to sleep.

NOTE: this script must itself run somewhere that is always on (a VPS, a Raspberry Pi, a home PC that
never sleeps, a cron service). If you have nowhere like that, use a free external pinger instead
(UptimeRobot or cron-job.org) pointed at the same /health URL.
"""
import argparse
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

DEFAULT_URL = "https://minster-livestream-monitor.onrender.com/health"
DEFAULT_INTERVAL = 600          # 10 minutes: safely under Render's 15-minute idle limit
TIMEOUT = 75                    # a cold start can take 30-60 s, so wait long enough for it
RETRY_AFTER = 30                # after a failed ping, try again soon instead of waiting 10 minutes
RETRIES = 3


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def ping(url: str) -> bool:
    """One GET. Any HTTP response below 500 proves the service is awake."""
    req = urllib.request.Request(url, headers={"User-Agent": "dominion-radio-keepalive/1.0"})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            print(f"[{stamp()}] OK {resp.status} in {time.monotonic() - t0:.1f}s", flush=True)
            return True
    except urllib.error.HTTPError as exc:
        ok = exc.code < 500
        print(f"[{stamp()}] {'OK' if ok else 'FAIL'} HTTP {exc.code} in {time.monotonic() - t0:.1f}s", flush=True)
        return ok
    except Exception as exc:
        print(f"[{stamp()}] FAIL {type(exc).__name__}: {exc}", flush=True)
        return False


def ping_with_retries(url: str) -> bool:
    for attempt in range(1, RETRIES + 1):
        if ping(url):
            return True
        if attempt < RETRIES:
            time.sleep(RETRY_AFTER)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=os.getenv("KEEP_ALIVE_URL", DEFAULT_URL))
    parser.add_argument("--interval", type=int, default=int(os.getenv("KEEP_ALIVE_INTERVAL", DEFAULT_INTERVAL)),
                        help="seconds between pings (default 600; keep it under 900)")
    parser.add_argument("--once", action="store_true", help="ping one time and exit")
    args = parser.parse_args()

    if not args.url.startswith(("https://", "http://")):
        parser.error("--url must start with https:// or http://")
    if args.interval >= 900:
        print("Warning: Render sleeps free services after 15 minutes (900 s); use a shorter interval.", file=sys.stderr)

    if args.once:
        return 0 if ping_with_retries(args.url) else 1

    print(f"Pinging {args.url} every {args.interval}s. Ctrl+C to stop.", flush=True)
    try:
        while True:
            ping_with_retries(args.url)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
