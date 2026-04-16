"""Tests for raw_converter module."""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pyimgtag.raw_converter import RAW_EXTENSIONS, extract_raw_thumbnail, is_raw


class TestIsRaw:
    def test_cr2_upper_returns_true(self):
        assert is_raw("photo.CR2") is True

    def test_cr3_lower_returns_true(self):
        assert is_raw("photo.cr3") is True

    def test_nef_upper_returns_true(self):
        assert is_raw("photo.NEF") is True

    def test_arw_lower_returns_true(self):
        assert is_raw("photo.arw") is True

    def test_raf_returns_true(self):
        assert is_raw("photo.raf") is True

    def test_orf_returns_true(self):
        assert is_raw("photo.orf") is True

    def test_rw2_returns_true(self):
        assert is_raw("photo.rw2") is True

    def test_dng_returns_true(self):
        assert is_raw("photo.dng") is True

    def test_pef_returns_true(self):
        assert is_raw("photo.pef") is True

    def test_jpg_returns_false(self):
        assert is_raw("photo.jpg") is False

    def test_heic_returns_false(self):
        assert is_raw("photo.heic") is False

    def test_png_returns_false(self):
        assert is_raw("photo.png") is False

    def test_raw_extensions_is_frozenset(self):
        assert isinstance(RAW_EXTENSIONS, frozenset)

    def test_raw_extensions_all_lowercase(self):
        for ext in RAW_EXTENSIONS:
            assert ext == ext.lower(), f"{ext!r} is not lowercase"

    def test_raw_extensions_all_start_with_dot(self):
        for ext in RAW_EXTENSIONS:
            assert ext.startswith("."), f"{ext!r} does not start with '.'"


class TestExtractRawThumbnail:
    _FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 200

    def _mock_proc(self, returncode: int = 0, stdout: bytes = _FAKE_JPEG) -> MagicMock:
        proc = MagicMock()
        proc.returncode = returncode
        proc.stdout = stdout
        return proc

    def test_returns_path_with_jpg_suffix(self, tmp_path: Path):
        src = tmp_path / "IMG_001.cr2"
        src.write_bytes(b"fake")
        with (
            patch("shutil.which", return_value="/usr/bin/exiftool"),
            patch("subprocess.run", return_value=self._mock_proc()),
        ):
            result = extract_raw_thumbnail(src, tmp_path)
        assert result.suffix == ".jpg"

    def test_output_inside_output_dir(self, tmp_path: Path):
        src = tmp_path / "IMG_001.cr2"
        src.write_bytes(b"fake")
        out_dir = tmp_path / "out"
        with (
            patch("shutil.which", return_value="/usr/bin/exiftool"),
            patch("subprocess.run", return_value=self._mock_proc()),
        ):
            result = extract_raw_thumbnail(src, out_dir)
        assert result.parent == out_dir

    def test_stem_is_input_stem_thumb(self, tmp_path: Path):
        src = tmp_path / "IMG_001.cr2"
        src.write_bytes(b"fake")
        with (
            patch("shutil.which", return_value="/usr/bin/exiftool"),
            patch("subprocess.run", return_value=self._mock_proc()),
        ):
            result = extract_raw_thumbnail(src, tmp_path)
        assert result.stem == "IMG_001_thumb"

    def test_writes_stdout_bytes_to_disk(self, tmp_path: Path):
        src = tmp_path / "IMG_001.cr2"
        src.write_bytes(b"fake")
        with (
            patch("shutil.which", return_value="/usr/bin/exiftool"),
            patch("subprocess.run", return_value=self._mock_proc()),
        ):
            result = extract_raw_thumbnail(src, tmp_path)
        assert result.read_bytes() == self._FAKE_JPEG

    def test_raises_runtime_error_when_exiftool_missing(self, tmp_path: Path):
        src = tmp_path / "IMG_001.cr2"
        src.write_bytes(b"fake")
        with patch("shutil.which", return_value=None):
            with pytest.raises(RuntimeError, match="exiftool"):
                extract_raw_thumbnail(src, tmp_path)

    def test_raises_file_not_found_when_input_missing(self, tmp_path: Path):
        src = tmp_path / "nonexistent.cr2"
        with patch("shutil.which", return_value="/usr/bin/exiftool"):
            with pytest.raises(FileNotFoundError):
                extract_raw_thumbnail(src, tmp_path)

    def test_first_subprocess_call_uses_jpg_from_raw(self, tmp_path: Path):
        src = tmp_path / "IMG_001.cr2"
        src.write_bytes(b"fake")
        with (
            patch("shutil.which", return_value="/usr/bin/exiftool"),
            patch("subprocess.run", return_value=self._mock_proc()) as mock_run,
        ):
            extract_raw_thumbnail(src, tmp_path)
        first_call_args = mock_run.call_args_list[0][0][0]
        assert "-JpgFromRaw" in first_call_args

    def test_falls_back_to_preview_image_when_first_empty(self, tmp_path: Path):
        src = tmp_path / "IMG_001.cr2"
        src.write_bytes(b"fake")
        empty_proc = self._mock_proc(returncode=0, stdout=b"")
        success_proc = self._mock_proc()
        side_effects = [empty_proc, success_proc]
        with (
            patch("shutil.which", return_value="/usr/bin/exiftool"),
            patch("subprocess.run", side_effect=side_effects) as mock_run,
        ):
            extract_raw_thumbnail(src, tmp_path)
        second_call_args = mock_run.call_args_list[1][0][0]
        assert "-PreviewImage" in second_call_args

    def test_raises_no_embedded_jpeg_when_all_tags_fail(self, tmp_path: Path):
        src = tmp_path / "IMG_001.cr2"
        src.write_bytes(b"fake")
        empty_proc = self._mock_proc(returncode=0, stdout=b"")
        with (
            patch("shutil.which", return_value="/usr/bin/exiftool"),
            patch("subprocess.run", return_value=empty_proc),
        ):
            with pytest.raises(RuntimeError, match="No embedded JPEG"):
                extract_raw_thumbnail(src, tmp_path)

    def test_creates_output_dir_including_parents(self, tmp_path: Path):
        src = tmp_path / "IMG_001.cr2"
        src.write_bytes(b"fake")
        out_dir = tmp_path / "a" / "b" / "c"
        with (
            patch("shutil.which", return_value="/usr/bin/exiftool"),
            patch("subprocess.run", return_value=self._mock_proc()),
        ):
            extract_raw_thumbnail(src, out_dir)
        assert out_dir.is_dir()

    def test_uses_temp_dir_when_output_dir_none(self, tmp_path: Path):
        src = tmp_path / "IMG_001.cr2"
        src.write_bytes(b"fake")
        with (
            patch("shutil.which", return_value="/usr/bin/exiftool"),
            patch("subprocess.run", return_value=self._mock_proc()),
        ):
            result = extract_raw_thumbnail(src, None)
        try:
            assert result.exists()
            assert result.suffix == ".jpg"
        finally:
            import shutil

            shutil.rmtree(result.parent, ignore_errors=True)
