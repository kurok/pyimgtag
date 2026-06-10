"""Tests for the screen-OCR face-naming path (``pyimgtag.face_ocr``).

The geometry (:func:`pair_faces_with_names`, :func:`_resized_dims`) is pure and
tested directly. The Vision-framework and screencapture calls are macOS-only, so
they are forced to fail deterministically (no pyobjc needed in CI) to exercise
the error paths, and :func:`build_references_from_screenshot` is tested with the
detector + OCR mocked so it runs without the ``[face]`` / ``[ocr]`` extras.
"""

from __future__ import annotations

import sys

import numpy as np
import pytest
from PIL import Image

from pyimgtag import face_ocr
from pyimgtag.face_ocr import (
    OcrText,
    OcrUnavailableError,
    _photos_window_id,
    _resized_dims,
    build_references_from_screenshot,
    capture_people_screenshot,
    pair_faces_with_names,
    recognize_text,
)
from pyimgtag.models import FaceDetection


def _text(name: str, cx: float, cy: float, w: float = 0.1, h: float = 0.03) -> OcrText:
    """An OcrText whose center sits at (cx, cy) in normalized top-left space."""
    return OcrText(text=name, x=cx - w / 2, y=cy - h / 2, w=w, h=h)


class TestOcrText:
    def test_center_properties(self):
        t = OcrText(text="Alice", x=0.2, y=0.4, w=0.1, h=0.02)
        assert t.center_x == pytest.approx(0.25)
        assert t.center_y == pytest.approx(0.41)


class TestResizedDims:
    def test_no_downscale_when_within_max_dim(self):
        assert _resized_dims(800, 600, 1280) == (800.0, 600.0)

    def test_downscales_longest_side_to_max_dim(self):
        # 2000x1000 with max_dim 1000 → scale 0.5 → 1000x500
        assert _resized_dims(2000, 1000, 1000) == (1000.0, 500.0)


class TestPairFacesWithNames:
    def test_two_tiles_each_get_their_caption(self):
        # Two side-by-side tiles; each name sits just below its face.
        faces = [(0.1, 0.125, 0.2, 0.25), (0.6, 0.125, 0.2, 0.25)]  # bottoms at 0.375
        texts = [_text("Alice", cx=0.2, cy=0.42), _text("Bob", cx=0.7, cy=0.42)]
        assert pair_faces_with_names(faces, texts) == [(0, "Alice"), (1, "Bob")]

    def test_face_without_caption_below_is_omitted(self):
        faces = [(0.1, 0.125, 0.2, 0.25)]
        # Caption is above the face, not below → no pair.
        texts = [_text("Ghost", cx=0.2, cy=0.05)]
        assert pair_faces_with_names(faces, texts) == []

    def test_caption_too_far_horizontally_is_not_paired(self):
        faces = [(0.1, 0.125, 0.2, 0.25)]  # center_x 0.2, spans 0.1..0.3
        texts = [_text("FarAway", cx=0.9, cy=0.42)]
        assert pair_faces_with_names(faces, texts) == []

    def test_caption_too_far_below_is_not_paired(self):
        faces = [(0.1, 0.1, 0.2, 0.2)]  # bottom 0.3
        texts = [_text("NextRow", cx=0.2, cy=0.8)]  # gap 0.5 > 0.18
        assert pair_faces_with_names(faces, texts) == []

    def test_a_caption_is_consumed_by_at_most_one_face(self):
        # Two faces compete for one caption; the vertically-closer one wins and
        # the other is left unnamed (no double assignment).
        faces = [(0.1, 0.1, 0.2, 0.2), (0.1, 0.3, 0.2, 0.2)]  # bottoms 0.3 and 0.5
        texts = [_text("Shared", cx=0.2, cy=0.55)]  # closest to face 1 (gap 0.05)
        assert pair_faces_with_names(faces, texts) == [(1, "Shared")]

    def test_names_are_stripped(self):
        faces = [(0.1, 0.125, 0.2, 0.25)]
        texts = [_text("  Spacey  ", cx=0.2, cy=0.42)]
        assert pair_faces_with_names(faces, texts) == [(0, "Spacey")]


class TestRecognizeText:
    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            recognize_text(tmp_path / "nope.png")

    def test_unavailable_when_pyobjc_missing(self, tmp_path, monkeypatch):
        img = tmp_path / "shot.png"
        Image.new("RGB", (10, 10)).save(img)
        # Force `import Quartz` to fail regardless of platform.
        monkeypatch.setitem(sys.modules, "Quartz", None)
        with pytest.raises(OcrUnavailableError):
            recognize_text(img)


class _FakeQuartz:
    """Minimal stand-in for the Quartz module used by `_photos_window_id`."""

    kCGWindowListOptionOnScreenOnly = 1
    kCGWindowListExcludeDesktopElements = 2
    kCGNullWindowID = 0

    def __init__(self, windows):
        self._windows = windows

    def CGWindowListCopyWindowInfo(self, _options, _relative_to):
        return self._windows


def _win(owner, number, w, h, layer=0):
    return {
        "kCGWindowOwnerName": owner,
        "kCGWindowLayer": layer,
        "kCGWindowNumber": number,
        "kCGWindowBounds": {"Width": w, "Height": h},
    }


class TestPhotosWindowId:
    def test_picks_largest_normal_photos_window(self):
        windows = [
            _win("Finder", 1, 4000, 4000),  # not Photos
            _win("Photos", 2, 500, 500, layer=25),  # a panel — skipped
            _win("Photos", 3, 100, 100),  # small
            _win("Photos", 4, 1200, 800),  # largest normal → winner
        ]
        assert _photos_window_id(_FakeQuartz(windows)) == 4

    def test_none_when_no_photos_window(self):
        assert _photos_window_id(_FakeQuartz([_win("Safari", 9, 100, 100)])) is None

    def test_handles_empty_window_list(self):
        assert _photos_window_id(_FakeQuartz(None)) is None


class TestCapturePeopleScreenshot:
    def test_requires_macos(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        with pytest.raises(OcrUnavailableError):
            capture_people_screenshot(tmp_path / "out.png")

    def test_unavailable_without_quartz_on_macos(self, tmp_path, monkeypatch):
        # On macOS but without the [ocr] extra: the Quartz import fails before
        # any subprocess runs, so this is deterministic on CI too.
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setitem(sys.modules, "Quartz", None)
        with pytest.raises(OcrUnavailableError):
            capture_people_screenshot(tmp_path / "out.png")

    def test_missing_screencapture_binary_raises_unavailable(self, tmp_path, monkeypatch):
        """Regression: a missing ``screencapture`` binary must surface as the
        documented OcrUnavailableError, not a raw FileNotFoundError."""
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setitem(sys.modules, "Quartz", _FakeQuartz([]))
        monkeypatch.setattr(face_ocr, "_photos_window_id", lambda _quartz: 42)

        def fake_run(cmd, **_kwargs):
            if cmd[0] == "screencapture":
                raise FileNotFoundError("screencapture not on PATH")
            return None  # osascript activate succeeds

        monkeypatch.setattr(face_ocr.subprocess, "run", fake_run)
        with pytest.raises(OcrUnavailableError):
            capture_people_screenshot(tmp_path / "out.png")


class TestBuildReferencesFromScreenshot:
    def _png(self, tmp_path, w=1000, h=800):
        p = tmp_path / "people.png"
        Image.new("RGB", (w, h), "white").save(p)
        return p

    def test_pairs_detected_faces_with_ocr_names(self, tmp_path, monkeypatch):
        shot = self._png(tmp_path)  # 1000x800, no downscale at max_dim 1280
        emb0, emb1 = np.ones(128), np.full(128, 2.0)
        detections = [
            (
                FaceDetection(
                    image_path=str(shot),
                    bbox_x=100,
                    bbox_y=100,
                    bbox_w=200,
                    bbox_h=200,
                    confidence=1.0,
                ),
                emb0,
            ),
            (
                FaceDetection(
                    image_path=str(shot),
                    bbox_x=600,
                    bbox_y=100,
                    bbox_w=200,
                    bbox_h=200,
                    confidence=1.0,
                ),
                emb1,
            ),
        ]
        # face0 → bottom 0.375, cx 0.2 ; face1 → bottom 0.375, cx 0.7
        ocr = [_text("Alice", cx=0.2, cy=0.42), _text("Bob", cx=0.7, cy=0.42)]
        monkeypatch.setattr("pyimgtag.face_embedding.detect_and_encode", lambda *a, **k: detections)
        monkeypatch.setattr("pyimgtag.face_ocr.recognize_text", lambda *a, **k: ocr)

        refs = build_references_from_screenshot(shot)
        assert set(refs) == {"Alice", "Bob"}
        assert np.array_equal(refs["Alice"][0], emb0)
        assert np.array_equal(refs["Bob"][0], emb1)

    def test_no_detections_returns_empty(self, tmp_path, monkeypatch):
        shot = self._png(tmp_path)
        monkeypatch.setattr("pyimgtag.face_embedding.detect_and_encode", lambda *a, **k: [])
        # recognize_text must not even be called when there are no faces.
        monkeypatch.setattr(
            "pyimgtag.face_ocr.recognize_text",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("called")),
        )
        assert build_references_from_screenshot(shot) == {}

    def test_face_without_embedding_is_skipped(self, tmp_path, monkeypatch):
        shot = self._png(tmp_path)
        detections = [
            (
                FaceDetection(
                    image_path=str(shot),
                    bbox_x=100,
                    bbox_y=100,
                    bbox_w=200,
                    bbox_h=200,
                    confidence=1.0,
                ),
                None,
            ),
        ]
        ocr = [_text("Alice", cx=0.2, cy=0.42)]
        monkeypatch.setattr("pyimgtag.face_embedding.detect_and_encode", lambda *a, **k: detections)
        monkeypatch.setattr("pyimgtag.face_ocr.recognize_text", lambda *a, **k: ocr)
        assert build_references_from_screenshot(shot) == {}


def test_face_ocr_module_exposes_public_api():
    # Guard against accidental rename of the names the CLI handler imports.
    for attr in (
        "build_references_from_screenshot",
        "capture_people_screenshot",
        "recognize_text",
        "pair_faces_with_names",
        "OcrUnavailableError",
    ):
        assert hasattr(face_ocr, attr)
