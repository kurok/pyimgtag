"""Tests for HEIC-to-JPEG conversion module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pyimgtag.heic_converter import convert_heic_to_jpeg, is_heic, sips_available


class TestIsHeic:
    def test_heic_lowercase(self):
        assert is_heic("photo.heic") is True

    def test_heic_uppercase(self):
        assert is_heic("photo.HEIC") is True

    def test_heif_lowercase(self):
        assert is_heif_true("photo.heif")

    def test_heif_uppercase(self):
        assert is_heic("photo.HEIF") is True

    def test_jpg_returns_false(self):
        assert is_heic("photo.jpg") is False

    def test_png_returns_false(self):
        assert is_heic("photo.png") is False

    def test_path_object(self):
        assert is_heic(Path("/some/dir/IMG_001.HEIC")) is True

    def test_no_extension(self):
        assert is_heic("README") is False


def is_heif_true(path: str) -> bool:
    """Helper to avoid confusion with the test method name."""
    return is_heic(path)


class TestSipsAvailable:
    @patch("pyimgtag.heic_converter.shutil.which", return_value="/usr/bin/sips")
    def test_returns_true_when_sips_found(self, mock_which: MagicMock) -> None:
        assert sips_available() is True
        mock_which.assert_called_once_with("sips")

    @patch("pyimgtag.heic_converter.shutil.which", return_value=None)
    def test_returns_false_when_sips_missing(self, mock_which: MagicMock) -> None:
        assert sips_available() is False


class TestConvertHeicToJpeg:
    @patch("pyimgtag.heic_converter.sips_available", return_value=True)
    @patch("pyimgtag.heic_converter.subprocess.run")
    def test_successful_conversion(
        self, mock_run: MagicMock, mock_sips: MagicMock, tmp_path: Path
    ) -> None:
        input_file = tmp_path / "photo.heic"
        input_file.write_bytes(b"fake heic data")
        output_dir = tmp_path / "out"

        # Make subprocess.run create the output file to simulate sips
        def fake_sips(*args, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            out = output_dir / "photo.jpg"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"fake jpeg data")
            return result

        mock_run.side_effect = fake_sips

        result = convert_heic_to_jpeg(input_file, output_dir=output_dir)

        assert result == output_dir / "photo.jpg"
        assert result.exists()
        mock_run.assert_called_once_with(
            ["sips", "-s", "format", "jpeg", str(input_file), "--out", str(result)],
            capture_output=True,
            text=True,
            timeout=30,
        )

    @patch("pyimgtag.heic_converter.sips_available", return_value=True)
    @patch("pyimgtag.heic_converter.subprocess.run")
    def test_uses_temp_dir_when_no_output_dir(
        self, mock_run: MagicMock, mock_sips: MagicMock, tmp_path: Path
    ) -> None:
        input_file = tmp_path / "photo.heic"
        input_file.write_bytes(b"fake heic data")

        def fake_sips(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            out_path = Path(cmd[-1])  # --out argument value
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"fake jpeg data")
            return result

        mock_run.side_effect = fake_sips

        result = convert_heic_to_jpeg(input_file)

        assert result.name == "photo.jpg"
        assert result.exists()
        assert "pyimgtag_heic_" in str(result.parent)

    @patch("pyimgtag.heic_converter.sips_available", return_value=True)
    @patch("pyimgtag.heic_converter.subprocess.run")
    def test_raises_on_nonzero_returncode(
        self, mock_run: MagicMock, mock_sips: MagicMock, tmp_path: Path
    ) -> None:
        input_file = tmp_path / "bad.heic"
        input_file.write_bytes(b"corrupt data")

        mock_run.return_value = MagicMock(returncode=1, stderr="Error: invalid format")

        with pytest.raises(RuntimeError, match="sips conversion failed"):
            convert_heic_to_jpeg(input_file, output_dir=tmp_path / "out")

    @patch("pyimgtag.heic_converter.sips_available", return_value=False)
    def test_raises_when_sips_not_available(self, mock_sips: MagicMock, tmp_path: Path) -> None:
        input_file = tmp_path / "photo.heic"
        input_file.write_bytes(b"fake data")

        with pytest.raises(RuntimeError, match="sips is not available"):
            convert_heic_to_jpeg(input_file)

    @patch("pyimgtag.heic_converter.sips_available", return_value=True)
    def test_raises_on_missing_input_file(self, mock_sips: MagicMock, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent.heic"

        with pytest.raises(FileNotFoundError, match="Input file not found"):
            convert_heic_to_jpeg(missing)

    @patch("pyimgtag.heic_converter.sips_available", return_value=True)
    @patch("pyimgtag.heic_converter.subprocess.run")
    def test_raises_when_output_not_created(
        self, mock_run: MagicMock, mock_sips: MagicMock, tmp_path: Path
    ) -> None:
        input_file = tmp_path / "photo.heic"
        input_file.write_bytes(b"fake data")

        # sips returns 0 but does not create the output file
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        with pytest.raises(RuntimeError, match="sips did not produce output file"):
            convert_heic_to_jpeg(input_file, output_dir=tmp_path / "out")
