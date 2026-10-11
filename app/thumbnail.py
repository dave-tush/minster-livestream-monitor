"""Reads the text printed on a livestream's thumbnail (usually the minister's name).

Why: the minister speaks at many churches, so there is no single trusted channel. The thumbnail is
extra evidence that works on ANY channel. Fetching a thumbnail costs no YouTube API quota.

Safety rules: this module never raises into the monitor. If the OCR engine is missing, the image
can't be downloaded, or reading fails, the stream is simply judged without thumbnail evidence.
Results are cached (by image content) so the same thumbnail is never read twice.

Engines (THUMBNAIL_OCR_ENGINE, default "auto" = rapidocr if installed, else tesseract):
  rapidocr   pip-only (`rapidocr` + `onnxruntime`). Default. Needs no system packages.
  tesseract  needs the `tesseract` program installed on the machine (apt/brew).
"""
import hashlib
import io
import logging
import subprocess
import threading
import time
from typing import Protocol

import httpx

from .models import LiveVideo

log = logging.getLogger("monitor")

MAX_IMAGE_BYTES = 5 * 1024 * 1024
CACHE_TTL_SECONDS = 900         # re-download a thumbnail at most every 15 min (owners do change them)
FAILURE_TTL_SECONDS = 300       # after a failed fetch/read, wait 5 min before trying that video again
MAX_CACHE_ENTRIES = 200


class OcrUnavailable(RuntimeError):
    """The chosen OCR engine isn't installed / can't start."""


class OcrEngine(Protocol):
    def read(self, image: bytes) -> str: ...


class RapidOcrEngine:
    """Uses the `rapidocr` package (works on current Python, incl. 3.13/3.14). Falls back to the
    older `rapidocr_onnxruntime` package, which only supports Python up to 3.12."""
    MIN_CONFIDENCE = 0.5

    def __init__(self):
        self._engine = None
        self._legacy = False

    def _load(self):
        logging.getLogger("RapidOCR").setLevel(logging.WARNING)     # it logs every model load at INFO
        try:
            from rapidocr import RapidOCR
        except ImportError:
            try:
                from rapidocr_onnxruntime import RapidOCR
                self._legacy = True
            except ImportError as exc:          # also covers opencv's "libGL.so.1" error on servers
                raise OcrUnavailable(
                    f"no OCR package is usable ({exc}). Install with: "
                    "python -m pip install -r requirements-ocr.txt  (see README: Thumbnail verification)"
                ) from exc
        self._engine = RapidOCR()

    def read(self, image: bytes) -> str:
        if self._engine is None:
            self._load()
        if self._legacy:
            result, _ = self._engine(image)
            lines = [(str(r[1]), float(r[2])) for r in (result or [])]
        else:
            out = self._engine(image)
            lines = list(zip(out.txts or (), out.scores or ()))
        return " ".join(text for text, score in lines if float(score) >= self.MIN_CONFIDENCE)


class TesseractEngine:
    """Reads text with the `tesseract` program.

    Tesseract is built for clean documents and struggles with text over busy photos, so the image is
    also read in two simplified forms (only the bright pixels / only the dark pixels, as black on
    white), and everything found is combined. The extra passes need Pillow; without it only the
    original image is read.
    """
    BRIGHT, DARK = 215, 60

    @staticmethod
    def _variants(image: bytes) -> list[bytes]:
        try:
            from PIL import Image, ImageOps
        except ImportError:
            return []
        try:
            gray = ImageOps.grayscale(Image.open(io.BytesIO(image)))
            out = []
            for mask in (gray.point(lambda v: 0 if v > TesseractEngine.BRIGHT else 255),
                         gray.point(lambda v: 0 if v < TesseractEngine.DARK else 255)):
                buf = io.BytesIO()
                mask.save(buf, "PNG")
                out.append(buf.getvalue())
            return out
        except Exception as exc:            # unreadable image: fall back to the original pass only
            log.debug("Could not prepare image variants for OCR: %s", exc)
            return []

    @staticmethod
    def _run(image: bytes) -> str:
        proc = subprocess.run(["tesseract", "stdin", "stdout", "--psm", "11"], input=image,
                              capture_output=True, timeout=30, check=True)
        return proc.stdout.decode("utf-8", "ignore")

    def read(self, image: bytes) -> str:
        try:
            parts = [self._run(image)]
        except FileNotFoundError as exc:
            raise OcrUnavailable("the `tesseract` program is not installed "
                                 "(on a Mac: brew install tesseract)") from exc
        for variant in self._variants(image):
            try:
                parts.append(self._run(variant))
            except (subprocess.SubprocessError, OSError):
                continue
        return "\n".join(" ".join(p.split()) for p in parts if p.strip())


class FallbackEngine:
    """Tries each engine in turn and uses the first one that is installed."""

    def __init__(self, engines: list):
        self.engines = engines
        self._active = None

    def read(self, image: bytes) -> str:
        if self._active is not None:
            return self._active.read(image)
        problems = []
        for engine in self.engines:
            try:
                text = engine.read(image)
            except OcrUnavailable as exc:
                problems.append(str(exc))
                continue
            self._active = engine
            log.info("Thumbnail OCR engine in use: %s", type(engine).__name__)
            return text
        raise OcrUnavailable("no OCR engine is installed. " + " | ".join(problems))


def make_engine(name: str) -> OcrEngine:
    name = (name or "auto").strip().lower()
    if name == "auto":
        return FallbackEngine([RapidOcrEngine(), TesseractEngine()])
    if name == "rapidocr":
        return RapidOcrEngine()
    if name == "tesseract":
        return TesseractEngine()
    raise ValueError(f"Unknown THUMBNAIL_OCR_ENGINE '{name}' (use auto, rapidocr or tesseract)")


class ThumbnailReader:
    def __init__(self, engine: OcrEngine, max_per_pass: int = 6, fetch=None, clock=time.monotonic):
        self.engine = engine
        self.max_per_pass = max_per_pass
        self._fetch = fetch or self._http_fetch
        self._clock = clock
        self._cache: dict[str, dict] = {}        # video_id -> {url, at, hash, text, ok}
        self._used = 0
        self._lock = threading.Lock()            # OCR is CPU/memory heavy: one image at a time
        self.disabled_reason: str | None = None

    def begin_pass(self) -> None:
        """Call at the start of each monitor pass: resets the per-pass OCR budget."""
        self._used = 0

    @staticmethod
    def _http_fetch(url: str) -> bytes:
        resp = httpx.get(url, timeout=10, follow_redirects=True)
        resp.raise_for_status()
        if not resp.headers.get("content-type", "").startswith("image/"):
            raise ValueError("thumbnail URL did not return an image")
        if len(resp.content) > MAX_IMAGE_BYTES:
            raise ValueError("thumbnail too large")
        return resp.content

    def read(self, video: LiveVideo) -> str | None:
        """Text found on the video's thumbnail, or None if unavailable. Never raises."""
        if self.disabled_reason or not video.thumbnail_url:
            return None
        now = self._clock()
        entry = self._cache.get(video.video_id)
        if entry and entry["url"] == video.thumbnail_url:
            ttl = CACHE_TTL_SECONDS if entry["ok"] else FAILURE_TTL_SECONDS
            if now - entry["at"] < ttl:
                return entry["text"]
        if self._used >= self.max_per_pass and not entry:
            return None          # budget for this pass is spent; try again next pass

        try:
            image = self._fetch(video.thumbnail_url)
            digest = hashlib.sha1(image).hexdigest()
            if entry and entry["ok"] and entry["hash"] == digest:
                entry["at"] = now            # same picture as before: no need to read it again
                return entry["text"]
            if self._used >= self.max_per_pass:
                return entry["text"] if entry and entry["ok"] else None
            self._used += 1
            with self._lock:
                text = self.engine.read(image)
            self._store(video, now, text, digest, ok=True)
            log.info("Thumbnail text for %s: %r", video.video_id, text[:120])
            return text
        except OcrUnavailable as exc:
            self.disabled_reason = str(exc)
            log.warning("Thumbnail verification DISABLED: %s", exc)
            return None
        except Exception as exc:            # network, bad image, engine crash: never break a check
            log.warning("Could not read thumbnail of %s: %s", video.video_id, exc)
            self._store(video, now, None, None, ok=False)
            return None

    def _store(self, video: LiveVideo, now: float, text, digest, ok: bool) -> None:
        if len(self._cache) >= MAX_CACHE_ENTRIES:
            for key in sorted(self._cache, key=lambda k: self._cache[k]["at"])[:MAX_CACHE_ENTRIES // 4]:
                del self._cache[key]
        self._cache[video.video_id] = {"url": video.thumbnail_url, "at": now, "hash": digest,
                                       "text": text, "ok": ok}
