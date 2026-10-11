"""Test the thumbnail check on one real video (best done while he is live):

    python -m app.thumbnail_check VIDEO_ID_OR_LINK

Shows the text read off the thumbnail, whether his name was found in it, and the final decision, so
you can tell if a missed stream is an OCR problem or a discovery problem. Costs 1 YouTube quota unit.
"""
import sys

from .config import Settings
from .matching import MIN_SCORE, evaluate, name_tokens, thumbnail_name_match
from .thumbnail import ThumbnailReader, make_engine
from .transcript_check import video_id_from
from .youtube import YouTubeClient


def run_check(video: str, settings: Settings, client=None, reader=None, out=print) -> bool:
    vid = video_id_from(video)
    names = [settings.minister_name, *settings.minister_aliases]
    out(f"Video: {vid}")
    out(f"Looking for: {', '.join(names)}")
    out(f"Distinctive name parts that must appear: {name_tokens(settings.minister_name)}\n")

    client = client or YouTubeClient(settings.youtube_api_key)
    videos = client.fetch_videos([vid])
    if not videos:
        out("FAILED  YouTube returned no video with that ID (check the link, and that it is public).")
        return False
    v = videos[0]
    out(f"Title:     {v.title}\nChannel:   {v.channel_title} [{v.channel_id}]")
    out(f"Live now:  {'yes' if v.is_live else 'NO (status: ' + str(v.broadcast_status) + ')'}")
    out(f"Thumbnail: {v.thumbnail_url or '(none)'}")

    reader = reader or ThumbnailReader(make_engine(settings.thumbnail_ocr_engine), max_per_pass=99)
    reader.begin_pass()
    text = reader.read(v)
    if text is None:
        out("\nFAILED  could not read the thumbnail. " + (reader.disabled_reason or
            "The image could not be downloaded or read; see the warning above."))
        return False
    out(f"\nText found on the thumbnail:\n  {text!r}")
    kind, used = thumbnail_name_match(text, names)
    out({"full": f"\nName on thumbnail: YES (matched '{used}')",
         "partial": "\nName on thumbnail: only PART of it (weak, not enough alone)",
         None: "\nName on thumbnail: NO"}[kind])

    match = evaluate(v, settings, text)
    out(f"\nDecision: score {match.score} (needs {MIN_SCORE}) -> {'ACCEPTED' if match.matched else 'rejected'}")
    out(f"Reasons:  {', '.join(match.reasons) or 'none'}")
    if not match.matched:
        out("\nIf his name IS on the thumbnail but it says NO, the OCR misread it: add the way it is printed "
            "to MINISTER_ALIASES.")
    elif not v.is_live:
        out("\nNote: it matched, but a stream is only picked up while it is live.")
    return True


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    sys.exit(0 if run_check(sys.argv[1], Settings.from_env()) else 1)
