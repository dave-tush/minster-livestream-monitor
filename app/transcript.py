"""Finds WHEN the minister comes up to minister, by reading the stream's transcript.

Runs after the thumbnail has confirmed the minister is on a stream. It looks for the minister's
name close to "cue" phrases that hosts use, e.g. "please welcome Pastor X", "come and minister",
and reports the most meaningful thing it found, strongest first:

  ministering  introduced, and preaching phrases ("turn with me to...") followed soon after
  introduced   named together with a clear hand-over phrase ("I present...", "give him a hand")
  welcomed     named with a weaker phrase ("welcome", "man of God"): probably greeted, maybe not up yet
  announced    named with a FUTURE phrase ("will minister", "after the offering"); a clock time
               is picked out when one is said ("at 10:30")
  mentioned    name heard, nothing more
  none         nothing found

Honest limits: auto-captions garble names (so matching is fuzzy) and a transcript only exists for
what has been spoken so far. For a stream with no captions nothing can be found; that is reported
as source "no_captions", never as a guess.
"""
import re
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field

from .matching import thumbnail_name_match

log = logging.getLogger("monitor")

WINDOW_SECONDS = 20            # a name and a cue count as "together" if said within this span
LEAD_IN_SECONDS = 8            # jump link starts a little before the cue so the intro isn't missed
PREACH_LOOKAHEAD_SECONDS = 900 # preaching phrases within 15 min after the intro confirm he is speaking
FETCH_TIMEOUT_SECONDS = 25

STATUS_RANK = {"none": 0, "mentioned": 1, "announced": 2, "welcomed": 3, "introduced": 4, "ministering": 5}


@dataclass
class Segment:
    start: float
    text: str


@dataclass
class Ministration:
    status: str = "none"
    timestamp_seconds: int | None = None        # where to jump in the video
    first_mention_seconds: int | None = None    # first time his name was heard at all
    evidence: str = ""                          # what was actually said
    announced_time: str | None = None           # e.g. "10:30 am", if a time was announced
    cues: list[str] = field(default_factory=list)

    @property
    def rank(self) -> int:
        return STATUS_RANK[self.status]


# ---- cue phrases ---------------------------------------------------------------------
# STRONG: he is being handed the platform right now.
STRONG_INTRO = [
    r"\bplease welcome\b", r"\bgive (?:him|her|the man of god)\b[^.]{0,25}\b(?:hand|clap|applause)\b",
    r"\bput (?:your|our|those) hands together\b", r"\bround of applause\b",
    r"\bcome(?:s)? (?:up )?(?:and|to) (?:minister|bless|share|speak|bring|preach)\b",
    r"\bi (?:now |would like to |want to |am (?:happy|pleased|delighted|honou?red) to )?(?:present|introduce)\b",
    r"\bit is my (?:great |distinct )?(?:honou?r|pleasure) to (?:present|introduce|welcome|call)\b",
    r"\b(?:take|taking|takes) (?:the|his) (?:stage|pulpit|podium|altar|microphone|mic)\b",
    r"\bover to (?:you )?(?:pastor|prophet|apostle|bishop|reverend|rev|dr|doctor|the man of god)\b",
    r"\b(?:bring|deliver|share|minister) (?:to us )?(?:the|a|his|today'?s) (?:word|message|sermon)\b",
    r"\bto minister (?:to us|the word|unto us)\b", r"\bwill now minister\b",
]
# WEAK: probably being greeted or described, not necessarily on the platform yet.
WEAK_INTRO = [
    r"\bwelcome\b", r"\bman of god\b", r"\b(?:guest|special) (?:minister|speaker|preacher|guest)\b",
    r"\bour (?:guest|speaker|preacher|minister)\b",
]
# FUTURE: he is expected later.
FUTURE = [
    r"\b(?:will|is going to|is gonna|shall|would) (?:be )?(?:minister|ministering|speak|speaking|preach|preaching|with us|join us|bring|share)\b",
    r"\b(?:coming up|up next|next up|later (?:on|today)|shortly|in a (?:few )?(?:minutes?|moments?))\b",
    r"\bafter the (?:offering|praise|worship|break|announcements|sermon|testimon(?:y|ies))\b",
    r"\bexpect(?:ing)? (?:him|her|pastor|prophet|apostle|bishop|the man of god)\b",
]
# After the intro: phrases a preacher says once he is speaking.
PREACHING = [
    r"\bturn (?:with me |your bibles? )?to\b", r"\bopen (?:your )?bibles?\b", r"\blet(?:'s| us) read\b",
    r"\b(?:the )?(?:bible|scripture|word of god) says\b", r"\btoday'?s (?:message|topic|sermon|text)\b",
    r"\bmy (?:message|topic|text|subject) (?:today|this|is)\b", r"\bthus saith\b",
]
TIME_MENTION = re.compile(
    r"\b(?:\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)|\d{1,2}\s*o'?clock|\d{1,2}:\d{2})", re.I)


def _compile(patterns: list[str]) -> list[re.Pattern]:
    return [re.compile(p, re.I) for p in patterns]


_STRONG, _WEAK, _FUTURE, _PREACH = map(_compile, (STRONG_INTRO, WEAK_INTRO, FUTURE, PREACHING))


def _hits(patterns: list[re.Pattern], text: str) -> list[str]:
    out = []
    for pat in patterns:
        m = pat.search(text)
        if m:
            out.append(m.group(0).lower())
    return out


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\n", " ")).strip()


# ---- analysis -------------------------------------------------------------------------
def analyze(segments: list[Segment], names: list[str], extra_keywords: list[str] | None = None) -> Ministration:
    """Work out what the transcript says about the minister. `names` = name + aliases."""
    segs = sorted((s for s in segments if s.text and s.text.strip()), key=lambda s: s.start)
    if not segs:
        return Ministration()
    strong = _STRONG + _compile([rf"\b{re.escape(k.strip())}\b" for k in (extra_keywords or []) if k.strip()])

    best_by_status: dict[str, Ministration] = {}
    first_mention: int | None = None
    n = len(segs)
    for i in range(n):
        j = i + 1
        while j < n and segs[j].start - segs[i].start <= WINDOW_SECONDS:
            j += 1
        window = segs[i:j]
        text = _clean(" ".join(s.text for s in window))

        c_strong, c_weak, c_future = _hits(strong, text), _hits(_WEAK, text), _hits(_FUTURE, text)
        has_cue = bool(c_strong or c_weak or c_future)
        kind, _variant = _name_in(text, names, fuzzy=has_cue)
        if kind is None:
            continue
        mention_at = int(next((s.start for s in window if _name_in(s.text, names, fuzzy=False)[0]), window[0].start))
        if first_mention is None or mention_at < first_mention:
            first_mention = mention_at

        # name + cue -> classify. A partial name (only first or last name) is trusted one level less.
        if c_strong:
            status, cues = ("introduced" if kind == "full" else "welcomed"), c_strong
        elif c_weak and kind == "full":
            status, cues = "welcomed", c_weak
        elif c_future and kind == "full":
            status, cues = "announced", c_future
        elif kind == "full":
            status, cues = "mentioned", []
        else:
            continue

        # Anchor to the cue of the kind we classified, nearest to where his name is said (not to
        # unrelated filler such as "welcome everyone" earlier in the window).
        used = strong if c_strong else (_WEAK if status == "welcomed" else _FUTURE)
        cue_segments = [s for s in window if _hits(used, s.text)] or window
        lead = [s for s in cue_segments if mention_at - 10 <= s.start <= mention_at]    # the build-up to his name
        cue_at = (min(lead, key=lambda s: s.start) if lead
                  else min(cue_segments, key=lambda s: abs(s.start - mention_at))).start
        if status in ("introduced", "welcomed"):
            ts = max(0, int(cue_at) - LEAD_IN_SECONDS)
        else:
            ts = int(window[0].start)
        found = Ministration(status=status, timestamp_seconds=ts, evidence=text[:240], cues=cues,
                             announced_time=(TIME_MENTION.search(text).group(0) if status in ("announced", "mentioned")
                                             and TIME_MENTION.search(text) else None))
        old = best_by_status.get(status)
        if old is None or ts < old.timestamp_seconds:        # keep the earliest of each kind
            best_by_status[status] = found

    if not best_by_status:
        return Ministration()
    top = max(best_by_status.values(), key=lambda m: m.rank)
    top.first_mention_seconds = first_mention

    if top.status == "introduced":
        after = [s for s in segs if top.timestamp_seconds <= s.start <= top.timestamp_seconds + PREACH_LOOKAHEAD_SECONDS]
        preaching = _hits(_PREACH, _clean(" ".join(s.text for s in after)))
        if preaching:
            top.status = "ministering"
            top.cues = top.cues + [f"then: {p}" for p in preaching[:2]]
    if top.status in ("welcomed", "mentioned", "introduced", "ministering") and "announced" in best_by_status:
        top.announced_time = top.announced_time or best_by_status["announced"].announced_time
    return top


def _name_in(text: str, names: list[str], fuzzy: bool) -> tuple[str | None, str]:
    """Is the minister named in `text`? Exact first (fast); the fuzzy search is only for cue windows."""
    kind, variant = thumbnail_name_match(text, names) if fuzzy else _exact_name(text, names)
    return kind, variant


def _exact_name(text: str, names: list[str]) -> tuple[str | None, str]:
    """Cheap whole-name check: every distinctive part appears as written (no fuzziness)."""
    from .matching import name_tokens, normalize
    squashed = normalize(text).replace(" ", "")
    for name in names:
        tokens = name_tokens(name)
        if tokens and all(t in squashed for t in tokens):
            return "full", name
    return None, ""


# ---- getting the transcript ------------------------------------------------------------
class YouTubeCaptions:
    """Reads a video's captions with the `youtube-transcript-api` package (optional dependency).

    fetch() never raises. It returns (segments, source_status) where source_status is
    ok | no_captions | blocked | unavailable | error.
    NOTE: YouTube often blocks requests that come from cloud servers; if so this reports "blocked".
    """

    def __init__(self, languages: list[str] | None = None, fetcher=None):
        self.languages = languages or ["en", "en-US", "en-GB"]
        self._fetcher = fetcher or self._library_fetch
        self._pool = ThreadPoolExecutor(max_workers=1)

    def _library_fetch(self, video_id: str) -> list[dict]:
        from youtube_transcript_api import YouTubeTranscriptApi
        if hasattr(YouTubeTranscriptApi, "fetch"):                       # version 1.x
            return YouTubeTranscriptApi().fetch(video_id, languages=self.languages).to_raw_data()
        return YouTubeTranscriptApi.get_transcript(video_id, languages=self.languages)   # 0.x

    def fetch(self, video_id: str) -> tuple[list[Segment], str]:
        future = self._pool.submit(self._fetcher, video_id)
        try:
            raw = future.result(timeout=FETCH_TIMEOUT_SECONDS)
        except FutureTimeout:
            return [], "error"
        except ImportError:
            log.warning("Transcript check needs: python -m pip install youtube-transcript-api")
            return [], "unavailable"
        except Exception as exc:
            name = type(exc).__name__
            if "Blocked" in name or "Cookie" in name:
                log.warning("YouTube is blocking transcript requests from this machine (%s).", name)
                return [], "blocked"
            if any(k in name for k in ("Disabled", "NoTranscript", "NotTranslatable", "Unavailable")):
                return [], "no_captions"
            log.warning("Transcript fetch failed for %s: %s: %s", video_id, name, str(exc)[:200])
            return [], "error"
        segments = [Segment(float(r["start"]), str(r["text"])) for r in raw if r.get("text")]
        return segments, ("ok" if segments else "no_captions")
