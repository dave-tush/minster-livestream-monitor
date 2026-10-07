"""Decides whether a live video really belongs to the minister.

Replace or extend `evaluate()` later to improve matching; nothing else depends on its internals.
Rule of thumb: a name appearing ONLY in the description is never enough.
"""
import re
import unicodedata

from .config import Settings
from .models import LiveVideo, MatchResult

MIN_SCORE = 40

SCORE_TRUSTED_CHANNEL = 100
SCORE_NAME_IN_TITLE = 50
SCORE_NAME_IN_CHANNEL = 40
SCORE_NAME_IN_DESCRIPTION = 0   # reported as a hint but adds no points: re-streamers stuff names into descriptions


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return " " + re.sub(r"[^a-z0-9]+", " ", text).strip() + " "


def _contains(name_norm: str, text: str) -> bool:
    return name_norm in normalize(text)


def evaluate(video: LiveVideo, settings: Settings) -> MatchResult:
    reasons: list[str] = []
    score = 0
    trusted = video.channel_id in settings.trusted_channel_ids

    if settings.trusted_only and not trusted:
        return MatchResult(False, 0, ["channel not in TRUSTED_CHANNEL_IDS (trusted_only)"])

    if trusted:
        score += SCORE_TRUSTED_CHANNEL
        reasons.append("trusted channel")

    name_norm = normalize(settings.minister_name)
    if _contains(name_norm, video.title):
        score += SCORE_NAME_IN_TITLE
        reasons.append("name in title")
    if _contains(name_norm, video.channel_title):
        score += SCORE_NAME_IN_CHANNEL
        reasons.append("name in channel title")
    if _contains(name_norm, video.description):
        score += SCORE_NAME_IN_DESCRIPTION
        reasons.append("name in description (weak)")

    return MatchResult(score >= MIN_SCORE, score, reasons)
