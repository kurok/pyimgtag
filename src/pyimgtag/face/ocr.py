"""Name auto-clustered people from a screenshot of Apple Photos' People view.

This is the "screen OCR" path: instead of reading the Photos library DB
(``import-photos`` / osxphotos) or a folder of labeled files
(``match-references``), it takes a *screenshot* of the People album — a grid of
face thumbnails each captioned with a name — and turns it into the same
``{name: [embedding]}`` reference map those paths produce. The face under each
tile is detected and embedded; the caption beneath it is read with macOS'
Vision OCR; the two are paired by position. The resulting references are fed to
the existing :func:`pyimgtag.face.naming.match_clusters_to_references` matcher.

Layering / testability:
  - :func:`pair_faces_with_names` is **pure** (geometry only) and unit-tested
    with synthetic boxes — it is the heart of the screenshot→name mapping.
  - :func:`recognize_text` wraps Apple's Vision framework (pyobjc, macOS only)
    and is mocked in tests; it raises :class:`OcrUnavailableError` off-macOS or
    when the ``[ocr]`` extra is missing.
  - :func:`capture_people_screenshot` drives Photos + ``screencapture`` for the
    ``--live`` mode; best-effort, macOS only, not exercised in CI.
  - :func:`build_references_from_screenshot` ties them together and additionally
    needs the ``[face]`` extra (the same detector/encoder as ``faces scan``).

All Vision bounding boxes are normalized ``[0, 1]`` with a *bottom-left* origin;
face detections come back in resized-image pixels. Everything is converted to
normalized *top-left* coordinates before pairing so the two never collide.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)

# Default vertical/horizontal tolerances for pairing a caption to its tile, as a
# fraction of image height/width. The People grid puts the name directly under
# the thumbnail, so the label's center is a short hop below the face and roughly
# in the same column. Generous enough for varied zoom levels, tight enough that
# a neighbouring tile's name does not get stolen.
_MAX_VERTICAL_GAP = 0.18
_MAX_HORIZONTAL_OFFSET = 0.12

_OCR_INSTALL_HINT = (
    "Screen OCR needs macOS and the [ocr] extra. Install with: pip install 'pyimgtag[ocr]'"
)


class OcrUnavailableError(RuntimeError):
    """Raised when macOS Vision OCR (or screen capture) cannot be used."""


@dataclass
class OcrText:
    """One recognized text run, in normalized top-left ``[0, 1]`` coordinates."""

    text: str
    x: float
    y: float
    w: float
    h: float

    @property
    def center_x(self) -> float:
        return self.x + self.w / 2

    @property
    def center_y(self) -> float:
        return self.y + self.h / 2


@dataclass
class _NormBox:
    """A normalized top-left face box plus its source detection index."""

    index: int
    x: float
    y: float
    w: float
    h: float

    @property
    def center_x(self) -> float:
        return self.x + self.w / 2

    @property
    def bottom(self) -> float:
        return self.y + self.h


def recognize_text(image_path: str | Path, *, languages: list[str] | None = None) -> list[OcrText]:
    """Run Apple's Vision OCR over *image_path* and return recognized text runs.

    Args:
        image_path: Image to read (a PNG screenshot, typically).
        languages: Optional BCP-47 hints, e.g. ``["ru-RU", "en-US"]``. Passing
            the right languages markedly improves non-Latin (e.g. Cyrillic)
            recognition over Vision's Latin-leaning default.

    Returns:
        Text runs in normalized top-left ``[0, 1]`` coordinates, in Vision's
        order.

    Raises:
        OcrUnavailableError: Off macOS, when the ``[ocr]`` extra is missing, or
            when Vision reports an error.
        FileNotFoundError: If *image_path* does not exist.
    """
    path = Path(image_path)
    if not path.is_file():
        raise FileNotFoundError(f"Image not found: {path}")

    try:
        import Quartz  # noqa: F401  (imported for its side-effect of loading CoreGraphics)
        import Vision
        from Foundation import NSURL
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise OcrUnavailableError(f"{_OCR_INSTALL_HINT} ({exc})") from exc

    url = NSURL.fileURLWithPath_(str(path))
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, None)
    request = Vision.VNRecognizeTextRequest.alloc().init()
    # Accurate level + language correction: we want clean names, not speed.
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setUsesLanguageCorrection_(True)
    if languages:
        request.setRecognitionLanguages_(languages)

    success, error = handler.performRequests_error_([request], None)
    if not success:
        raise OcrUnavailableError(f"Vision OCR failed: {error}")

    out: list[OcrText] = []
    for obs in request.results() or []:
        candidates = obs.topCandidates_(1)
        if not candidates:
            continue
        text = candidates[0].string()
        if not text:
            continue
        box = obs.boundingBox()  # normalized, bottom-left origin
        ox = float(box.origin.x)
        oy = float(box.origin.y)
        bw = float(box.size.width)
        bh = float(box.size.height)
        # bottom-left → top-left: y_top = 1 - (origin.y + height)
        out.append(OcrText(text=str(text), x=ox, y=1.0 - (oy + bh), w=bw, h=bh))
    return out


def _resized_dims(width: int, height: int, max_dim: int) -> tuple[float, float]:
    """Mirror :func:`pyimgtag.face.detection._load_and_resize` scaling.

    Face bboxes are stored in the *resized* image's pixel space, so to normalize
    them we need the same dimensions the detector saw.
    """
    longest = max(width, height)
    if longest > max_dim:
        scale = max_dim / longest
        return width * scale, height * scale
    return float(width), float(height)


def pair_faces_with_names(
    faces: list[tuple[float, float, float, float]],
    texts: list[OcrText],
    *,
    max_vertical_gap: float = _MAX_VERTICAL_GAP,
    max_horizontal_offset: float = _MAX_HORIZONTAL_OFFSET,
) -> list[tuple[int, str]]:
    """Pair each face with the caption directly beneath its tile (pure geometry).

    Args:
        faces: Normalized top-left ``(x, y, w, h)`` boxes, one per detected face.
        texts: Recognized text runs (normalized top-left).
        max_vertical_gap: Max distance (fraction of height) from a face's bottom
            edge down to a caption's center for them to be considered a pair.
        max_horizontal_offset: How far (fraction of width) a caption's center may
            sit outside the face's horizontal extent and still belong to it.

    Returns:
        ``(face_index, name)`` pairs, sorted by ``face_index``. Faces with no
        caption beneath them (unnamed tiles) are omitted, and each caption is
        consumed by at most one face (the nearest), so two tiles never share a
        name.
    """
    boxes = [_NormBox(i, x, y, w, h) for i, (x, y, w, h) in enumerate(faces)]

    # Build all viable (face, text) candidates with a distance score, then assign
    # greedily from closest to farthest so the best geometric fit wins ties.
    candidates: list[tuple[float, int, int]] = []  # (score, face_index, text_index)
    for box in boxes:
        for ti, t in enumerate(texts):
            v_gap = t.center_y - box.bottom
            if v_gap < -box.h * 0.5 or v_gap > max_vertical_gap:
                # Caption must be below the tile (a little overlap tolerated), not
                # above it and not in the next row down.
                continue
            left = box.x - max_horizontal_offset
            right = box.x + box.w + max_horizontal_offset
            if not (left <= t.center_x <= right):
                continue
            h_dist = abs(t.center_x - box.center_x)
            # Vertical proximity dominates; horizontal breaks ties.
            score = max(v_gap, 0.0) + h_dist * 0.25
            candidates.append((score, box.index, ti))

    candidates.sort(key=lambda c: c[0])
    used_faces: set[int] = set()
    used_texts: set[int] = set()
    pairs: list[tuple[int, str]] = []
    for _score, fi, ti in candidates:
        if fi in used_faces or ti in used_texts:
            continue
        used_faces.add(fi)
        used_texts.add(ti)
        pairs.append((fi, texts[ti].text.strip()))

    pairs.sort(key=lambda p: p[0])
    return pairs


def build_references_from_screenshot(
    image_path: str | Path,
    *,
    max_dim: int = 1280,
    model: str = "hog",
    num_jitters: int = 1,
    languages: list[str] | None = None,
) -> dict[str, list[np.ndarray]]:
    """Detect + embed faces in a People-view screenshot and label them by OCR.

    Returns ``{person_name: [embedding, ...]}`` ready for
    :func:`pyimgtag.face.naming.match_clusters_to_references`. Needs both the
    ``[face]`` and ``[ocr]`` extras at runtime.
    """
    from collections import defaultdict

    from PIL import Image

    from pyimgtag.face.embedding import detect_and_encode

    path = Path(image_path)
    detections = detect_and_encode(path, max_dim=max_dim, model=model, num_jitters=num_jitters)
    if not detections:
        logger.warning("screenshot %s: no faces detected", path)
        return {}

    with Image.open(path) as im:
        width, height = im.size
    rw, rh = _resized_dims(width, height, max_dim)

    norm_faces: list[tuple[float, float, float, float]] = []
    for det, _emb in detections:
        norm_faces.append((det.bbox_x / rw, det.bbox_y / rh, det.bbox_w / rw, det.bbox_h / rh))

    texts = recognize_text(path, languages=languages)
    pairs = pair_faces_with_names(norm_faces, texts)

    refs: dict[str, list[np.ndarray]] = defaultdict(list)
    for face_index, name in pairs:
        if not name:
            continue
        _det, embedding = detections[face_index]
        if embedding is None:
            continue
        refs[name].append(embedding)
    return dict(refs)


def _photos_window_id(quartz) -> int | None:
    """Return the window number of Apple Photos' largest normal window, or None.

    Uses the on-screen window list so we can capture by **window id** rather than
    by screen rectangle — robust on multi-display setups where the window lives
    at negative global coordinates (a rectangle capture with `screencapture -R`
    fails there). ``quartz`` is injected so the geometry is unit-testable with a
    fake module.
    """
    options = quartz.kCGWindowListOptionOnScreenOnly | quartz.kCGWindowListExcludeDesktopElements
    windows = quartz.CGWindowListCopyWindowInfo(options, quartz.kCGNullWindowID) or []
    best_id: int | None = None
    best_area = -1.0
    for w in windows:
        if w.get("kCGWindowOwnerName") != "Photos":
            continue
        # Layer 0 = a normal app window; skip menubar items, panels, tooltips.
        if w.get("kCGWindowLayer", 0) != 0:
            continue
        bounds = w.get("kCGWindowBounds", {})
        area = float(bounds.get("Width", 0)) * float(bounds.get("Height", 0))
        if area > best_area and w.get("kCGWindowNumber") is not None:
            best_area = area
            best_id = int(w["kCGWindowNumber"])
    return best_id


def capture_people_screenshot(out_path: str | Path) -> Path:
    """Bring Apple Photos forward and screenshot its window (macOS, best-effort).

    Activates Photos, finds its window via CoreGraphics, and captures **that
    window by id** (`screencapture -l`) — which works regardless of which display
    the window is on, including secondary displays at negative coordinates. The
    caller is responsible for having the People album visible. Returns the
    written path.

    Raises:
        OcrUnavailableError: Off macOS, when the ``[ocr]`` extra is missing, or
            if Photos / ``screencapture`` fail (e.g. missing Screen Recording
            permission, or no Photos window open).
    """
    import sys

    if sys.platform != "darwin":
        raise OcrUnavailableError("Live capture needs macOS (Apple Photos + screencapture).")

    out = Path(out_path)
    try:
        import Quartz
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise OcrUnavailableError(f"{_OCR_INSTALL_HINT} ({exc})") from exc

    try:
        subprocess.run(
            ["osascript", "-e", 'tell application "Photos" to activate'],
            check=True,
            capture_output=True,
            timeout=15,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        raise OcrUnavailableError(f"Could not bring Apple Photos to the front: {exc}") from exc

    window_id = _photos_window_id(Quartz)
    if window_id is None:
        raise OcrUnavailableError(
            "No Apple Photos window found. Open Photos and show the People album, "
            "then retry --live (or pass --screenshot with a saved screenshot)."
        )

    try:
        # -l <id>: capture exactly that window; -o: no window shadow.
        subprocess.run(
            ["screencapture", "-x", "-o", "-l", str(window_id), str(out)],
            check=True,
            capture_output=True,
            timeout=30,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        raise OcrUnavailableError(
            f"Could not capture the Photos window: {exc}. If this persists, grant "
            "Screen Recording permission to your terminal under System Settings → "
            "Privacy & Security → Screen Recording, then retry."
        ) from exc
    if not out.is_file():
        raise OcrUnavailableError("screencapture produced no file.")
    return out
