"""Tests for face_thumb module."""

from __future__ import annotations

import base64
import io

from PIL import Image

from pyimgtag.face_thumb import face_thumbnail_b64


class TestFaceThumbnailB64:
    def _make_image(self, tmp_path, width=200, height=200, color=(128, 64, 32)):
        img = Image.new("RGB", (width, height), color=color)
        path = tmp_path / "face.jpg"
        img.save(str(path), "JPEG")
        return str(path)

    def test_returns_base64_string(self, tmp_path):
        path = self._make_image(tmp_path)
        result = face_thumbnail_b64(path, bbox_x=50, bbox_y=50, bbox_w=80, bbox_h=80)
        assert result is not None
        data = base64.b64decode(result)
        img = Image.open(io.BytesIO(data))
        assert img.format == "JPEG"

    def test_output_is_square(self, tmp_path):
        path = self._make_image(tmp_path)
        result = face_thumbnail_b64(path, bbox_x=20, bbox_y=20, bbox_w=60, bbox_h=60, size=80)
        assert result is not None
        data = base64.b64decode(result)
        img = Image.open(io.BytesIO(data))
        assert img.size == (80, 80)

    def test_missing_file_returns_none(self, tmp_path):
        result = face_thumbnail_b64(
            str(tmp_path / "nonexistent.jpg"), bbox_x=0, bbox_y=0, bbox_w=50, bbox_h=50
        )
        assert result is None

    def test_bbox_clamped_to_image_bounds(self, tmp_path):
        path = self._make_image(tmp_path, width=100, height=100)
        # bbox goes outside image — should still succeed with clamping
        result = face_thumbnail_b64(path, bbox_x=80, bbox_y=80, bbox_w=50, bbox_h=50)
        assert result is not None

    def test_zero_size_bbox_returns_none(self, tmp_path):
        path = self._make_image(tmp_path)
        result = face_thumbnail_b64(path, bbox_x=0, bbox_y=0, bbox_w=0, bbox_h=0)
        assert result is None
