"""Tests for face_thumb module."""

from __future__ import annotations

import base64
import io
from pathlib import Path
from unittest.mock import patch

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

    def test_bbox_scaled_for_large_image(self, tmp_path):
        # Image is 4x the detection max (1280) on the long side.
        # A face at detection-space coords (100, 100, 80, 80) should be
        # scaled up to ~(400, 400, 320, 320) in full-image space.
        # Paint a bright red face region at the expected full-image crop site
        # and a black background everywhere else — if scaling is wrong the
        # crop will hit the black background and return a near-black thumbnail.
        full_w, full_h = 5120, 3840  # long side = 5120 → detect scale = 1280/5120 = 0.25
        # det bbox (100, 100, 80, 80) → full-image coords ≈ (400, 400, 320, 320)
        img = Image.new("RGB", (full_w, full_h), color=(0, 0, 0))
        face_region = Image.new("RGB", (320, 320), color=(255, 0, 0))
        img.paste(face_region, (400, 400))
        path = tmp_path / "large.jpg"
        img.save(str(path), "JPEG")

        result = face_thumbnail_b64(str(path), bbox_x=100, bbox_y=100, bbox_w=80, bbox_h=80)
        assert result is not None
        data = base64.b64decode(result)
        thumb = Image.open(io.BytesIO(data)).convert("RGB")
        r_bytes = thumb.split()[0].tobytes()
        avg_r = sum(r_bytes) / len(r_bytes)
        # If bbox was correctly scaled the crop hits the red region → avg red > 80
        # (JPEG compression reduces peak values slightly).
        # If bbox was not scaled the crop hits the black background → avg red ≈ 0.
        assert avg_r > 80, (
            f"Expected bright red crop (avg_r={avg_r:.1f}), got dark — bbox not scaled"
        )

    def test_heic_source_is_converted(self, tmp_path):
        """HEIC inputs are routed through convert_heic_to_jpeg before cropping.

        is_heic/convert_heic_to_jpeg are mocked at their module boundary so the
        HEIC branch runs even on platforms without sips (e.g. CI on Linux).
        The mock mirrors the real contract: a Path to a JPEG inside a fresh
        temp dir that the caller owns — and must clean up after reading.
        """
        # The actual pixels live in this JPEG; the .heic path is only a label.
        owned_dir = tmp_path / "pyimgtag_heic_fake"
        owned_dir.mkdir()
        real = Path(self._make_image(owned_dir, width=200, height=200, color=(10, 200, 30)))
        heic_path = str(tmp_path / "photo.heic")

        with (
            patch("pyimgtag.heic_converter.is_heic", return_value=True),
            patch("pyimgtag.heic_converter.convert_heic_to_jpeg", return_value=real) as mock_conv,
        ):
            result = face_thumbnail_b64(heic_path, bbox_x=40, bbox_y=40, bbox_w=80, bbox_h=80)

        mock_conv.assert_called_once_with(heic_path)
        assert result is not None
        thumb = Image.open(io.BytesIO(base64.b64decode(result))).convert("RGB")
        assert thumb.format is None or thumb.size == (120, 120)
        # Regression: the owned temp conversion dir used to be leaked.
        assert not owned_dir.exists()

    def test_degenerate_crop_after_clamp_returns_none(self, tmp_path):
        """A bbox fully off the right/bottom edge collapses the crop → None.

        With bbox_x/bbox_y beyond the image and zero padding the clamped
        ``right <= left`` / ``bottom <= top`` guard (line 76) must fire.
        """
        path = self._make_image(tmp_path, width=100, height=100)
        # bbox starts at the far edge; padding 0 so left=100 right=min(100,100)=100
        result = face_thumbnail_b64(path, bbox_x=100, bbox_y=100, bbox_w=10, bbox_h=10, padding=0.0)
        assert result is None
