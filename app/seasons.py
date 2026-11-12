"""Seasons: pick the sermons that belong to a weekly / seasonal emphasis such as "Consecration".

A season's playlist is the union of
  * sermons whose TITLE contains one of the season's keywords (matched at the start of a word, so
    "consecration" also finds "Consecrate"/"Consecrated" but "fast" does not find "breakfast"), and
  * sermons the administrator ticked by hand.

Keyword matches that are the same sermon uploaded twice (same title and length) are listed once.
Hand-picked sermons are always kept exactly as chosen. Everything here is pure (no database, no
clock), so it is easy to test.
"""
import re
import unicodedata

# Longest first, so "consecration" loses "ation" rather than just "ion".
_SUFFIXES = ("ations", "ation", "ings", "ing", "ions", "ion", "ness", "ed", "es", "s", "e")
MAX_KEYWORDS = 20
MAX_KEYWORD_LEN = 60


def norm(text: str) -> str:
    """Lower-case, accents removed, punctuation turned into single spaces."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def clean_keywords(raw) -> list[str]:
    """Accepts a list or a comma/semicolon/newline separated string; returns tidy unique keywords."""
    if isinstance(raw, str):
        raw = re.split(r"[,;\n]+", raw)
    out: list[str] = []
    seen: set[str] = set()
    for item in raw or []:
        kw = re.sub(r"\s+", " ", str(item)).strip()[:MAX_KEYWORD_LEN]
        key = norm(kw)
        if key and key not in seen:
            seen.add(key)
            out.append(kw)
        if len(out) >= MAX_KEYWORDS:
            break
    return out


def _stem(word: str) -> str:
    """Strip one common ending ("consecration" -> "consecr") but never leave fewer than 5 letters."""
    for suf in _SUFFIXES:
        if word.endswith(suf) and len(word) - len(suf) >= 5:
            return word[: -len(suf)]
    return word


def _needles(keywords: list[str]) -> list[str]:
    needles = []
    for kw in keywords:
        n = norm(kw)
        if not n:
            continue
        # Only a single word is stemmed; a phrase ("man after god's heart") must match as written.
        needles.append(_stem(n) if " " not in n else n)
    return needles


def title_matches(title: str, keywords: list[str]) -> bool:
    haystack = " " + norm(title)
    return any(" " + needle in haystack for needle in _needles(keywords))


def build_playlist(tracks: list[dict], keywords: list[str], picked_ids: list[int] | set[int]) -> tuple[list[int], list[int]]:
    """Returns (playlist ids in broadcast order, ids matched by keyword).

    `tracks` are dicts with message_id, title and duration. Order is oldest upload first, the same
    order the whole station uses.
    """
    picked = set(picked_ids or [])
    ordered = sorted(tracks, key=lambda t: t["message_id"])
    seen = {(norm(t["title"]), t["duration"]) for t in ordered if t["message_id"] in picked}
    playlist: list[int] = []
    matched: list[int] = []
    for t in ordered:
        mid = t["message_id"]
        if mid in picked:
            playlist.append(mid)
            if keywords and title_matches(t["title"], keywords):
                matched.append(mid)
            continue
        if keywords and title_matches(t["title"], keywords):
            matched.append(mid)
            key = (norm(t["title"]), t["duration"])
            if key in seen:
                continue                      # the same sermon uploaded twice: play it once
            seen.add(key)
            playlist.append(mid)
    return playlist, matched
