"""Decides whether a live video really belongs to the minister.

Replace or extend `evaluate()` later to improve matching; nothing else depends on its internals.
Rule of thumb: a name appearing ONLY in the description is never enough.
"""
import re
import unicodedata
from difflib import SequenceMatcher

from .config import Settings
from .models import LiveVideo, MatchResult

MIN_SCORE = 40

SCORE_TRUSTED_CHANNEL = 100
SCORE_NAME_IN_TITLE = 50
SCORE_NAME_IN_CHANNEL = 40
SCORE_NAME_ON_THUMBNAIL = 50    # same weight as the title: the name is printed on the thumbnail image
SCORE_PART_OF_NAME_ON_THUMBNAIL = 15   # e.g. only the surname was readable: helps ranking, never enough alone
SCORE_NAME_IN_DESCRIPTION = 0   # reported as a hint but adds no points: re-streamers stuff names into descriptions


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return " " + re.sub(r"[^a-z0-9]+", " ", text).strip() + " "


def _contains(name_norm: str | list[str], text: str) -> bool:
    """Is the name in the text? Accepts one normalised name or several variants."""
    variants = [name_norm] if isinstance(name_norm, str) else name_norm
    haystack = normalize(text)
    return any(v and v in haystack for v in variants)


def search_name(name: str) -> str:
    """The name without titles (Pastor, Dr...), used as the YouTube search query and for matching."""
    return " ".join(name_tokens(name)) or normalize(name)


# ---- reading the minister's name out of thumbnail text (OCR output) ------------------
HONORIFICS = {"pastor", "pst", "ps", "prophet", "prophetess", "apostle", "ap", "rev", "reverend",
              "dr", "doctor", "bishop", "evangelist", "minister", "mr", "mrs", "mister", "elder",
              "deacon", "prof", "professor", "sir", "chief", "oba"}
# Characters OCR commonly confuses with letters (applied to the OCR text only).
OCR_LOOKALIKES = str.maketrans({"0": "o", "1": "l", "3": "e", "4": "a", "5": "s", "8": "b"})


def name_tokens(name: str) -> list[str]:
    """The distinctive parts of a name: honorifics (Pastor, Dr...) are dropped."""
    # Honorifics (Pastor, Dr...) and stray single letters (the "P" in "P Daniel Olawande") are not
    # part of the name, so they must not be required to match.
    return [t for t in normalize(name).split() if t not in HONORIFICS and len(t) > 1]


def _token_in_text(token: str, squashed: str, words: set[str]) -> bool:
    """Is `token` in the OCR text, allowing a few misread characters?

    OCR engines often run words together ("PASTORTOLUWALOGO"), so we search the text with spaces
    removed. Longer names tolerate more errors; very short ones must match a whole word exactly.
    """
    n = len(token)
    if n < 4:
        return token in words
    if token in squashed:
        return True
    edits = 0 if n <= 5 else 1 if n <= 9 else 2
    if edits == 0:
        return False
    # Speed-up that loses no matches: if the token is cut into (edits + 1) pieces, at least one piece
    # must appear unchanged in any text that is within `edits` mistakes of it. So only look closely
    # around places where a piece occurs, instead of comparing against every position.
    threshold = (n - edits) / n - 1e-9
    # Cut the token into (edits + 1) near-equal pieces (each at least 3 letters for the sizes that reach here).
    cuts = [round(i * n / (edits + 1)) for i in range(edits + 2)]
    for offset, end in zip(cuts, cuts[1:]):
        piece = token[offset:end]
        pos = squashed.find(piece)
        while pos != -1:
            for start in range(max(0, pos - offset - edits), pos - offset + edits + 1):
                for width in range(n - edits, n + edits + 1):
                    if start + width <= len(squashed) and SequenceMatcher(
                            None, token, squashed[start:start + width]).ratio() >= threshold:
                        return True
            pos = squashed.find(piece, pos + 1)
    return False


def thumbnail_name_match(text: str | None, names: list[str]) -> tuple[str | None, str]:
    """Look for the minister (or any alias) in thumbnail text.

    Returns ("full" | "partial" | None, the name variant that matched).
    full    = every distinctive part of the name is there (all but one for 3+ part names)
    partial = some parts only, e.g. just the surname
    """
    if not text or not text.strip():
        return None, ""
    lowered = normalize(text)
    fixed = normalize(text.lower().translate(OCR_LOOKALIKES))
    words = set(lowered.split()) | set(fixed.split())
    squashed = fixed.replace(" ", "")
    best: tuple[str | None, str] = (None, "")
    for name in names:
        tokens = name_tokens(name)
        if not tokens:
            continue
        hits = sum(_token_in_text(t, squashed, words) for t in tokens)
        need = len(tokens) if len(tokens) <= 2 else len(tokens) - 1
        if hits >= need:
            return "full", name
        if hits >= 1 and best[0] is None:
            best = ("partial", name)
    return best


def evaluate(video: LiveVideo, settings: Settings, thumbnail_text: str | None = None) -> MatchResult:
    """Score a live video. `thumbnail_text` is OCR text read from its thumbnail (optional)."""
    reasons: list[str] = []
    score = 0
    trusted = video.channel_id in settings.trusted_channel_ids

    if settings.trusted_only and not trusted:
        return MatchResult(False, 0, ["channel not in TRUSTED_CHANNEL_IDS (trusted_only)"])

    if trusted:
        score += SCORE_TRUSTED_CHANNEL
        reasons.append("trusted channel")

    # Match the name as written AND without titles, so "Pastor Daniel Olawande" in a title matches a
    # configured name of "P Daniel Olawande" (and vice versa).
    name_norm = list(dict.fromkeys([normalize(settings.minister_name), search_name(settings.minister_name)]))
    if _contains(name_norm, video.title):
        score += SCORE_NAME_IN_TITLE
        reasons.append("name in title")
    if _contains(name_norm, video.channel_title):
        score += SCORE_NAME_IN_CHANNEL
        reasons.append("name in channel title")
    if _contains(name_norm, video.description):
        score += SCORE_NAME_IN_DESCRIPTION
        reasons.append("name in description (weak)")

    if thumbnail_text:
        kind, variant = thumbnail_name_match(
            thumbnail_text, [settings.minister_name, *settings.minister_aliases])
        if kind == "full":
            score += SCORE_NAME_ON_THUMBNAIL
            reasons.append("name on thumbnail" + (f" (as '{variant}')" if variant != settings.minister_name else ""))
        elif kind == "partial":
            score += SCORE_PART_OF_NAME_ON_THUMBNAIL
            reasons.append("part of name on thumbnail (weak)")

    return MatchResult(score >= MIN_SCORE, score, reasons)
