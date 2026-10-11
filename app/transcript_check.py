"""Try the transcript check on any video (a past service is the best test):

    python -m app.transcript_check VIDEO_ID_OR_URL

Prints whether captions could be read, when the minister was introduced, what was said, and a link
that jumps to that moment, so you can compare it with what you know really happened.
"""
import re
import sys

from .config import Settings
from .transcript import YouTubeCaptions, analyze


def video_id_from(text: str) -> str:
    m = re.search(r"(?:v=|youtu\.be/|/live/|/embed/)([A-Za-z0-9_-]{11})", text)
    return m.group(1) if m else text.strip()


def _clock(seconds: int | None) -> str:
    if seconds is None:
        return "-"
    h, rem = divmod(int(seconds), 3600)
    return f"{h}:{rem // 60:02d}:{rem % 60:02d}"


def run_check(video: str, settings: Settings, captions=None, out=print) -> bool:
    vid = video_id_from(video)
    captions = captions or YouTubeCaptions(settings.transcript_languages)
    names = [settings.minister_name, *settings.minister_aliases]
    out(f"Video: {vid}\nLooking for: {', '.join(names)}\n")
    segments, source = captions.fetch(vid)
    if not segments:
        reason = {"no_captions": "this video has no captions (yet)",
                  "blocked": "YouTube is blocking transcript requests from this network",
                  "unavailable": "the youtube-transcript-api package is not installed "
                                 "(python -m pip install youtube-transcript-api)"}.get(source, "the transcript could not be read")
        out(f"FAILED  {reason}  [source: {source}]")
        return False
    out(f"OK      read {len(segments)} caption lines covering {_clock(int(segments[-1].start))}")
    result = analyze(segments, names, settings.ministration_keywords)
    out(f"\nResult: {result.status.upper()}")
    if result.status == "none":
        out("The minister's name was not heard together with any hand-over phrase.")
        out("(Captions may have written the name very differently. Add short forms to MINISTER_ALIASES.)")
        return True
    out(f"  jump to:        {_clock(result.timestamp_seconds)}   https://www.youtube.com/watch?v={vid}&t={result.timestamp_seconds}s")
    out(f"  first heard:    {_clock(result.first_mention_seconds)}")
    if result.announced_time:
        out(f"  announced time: {result.announced_time}")
    out(f"  phrases:        {', '.join(result.cues) or '-'}")
    out(f"  heard:          \"{result.evidence}\"")
    return True


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    sys.exit(0 if run_check(sys.argv[1], Settings.from_env()) else 1)
