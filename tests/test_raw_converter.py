"""Tests for raw_converter module."""

from __future__ import annotations

import importlib
import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pyimgtag.raw_converter import (
    RAW_EXTENSIONS,
    convert_raw_with_rawpy,
    extract_raw_thumbnail,
    is_raw,
    rawpy_available,
)


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
            patch("pyimgtag.raw_converter.shutil.which", return_value="/usr/bin/exiftool"),
            patch("pyimgtag.raw_converter.subprocess.run", return_value=self._mock_proc()),
        ):
            result = extract_raw_thumbnail(src, tmp_path)
        assert result.suffix == ".jpg"

    def test_output_inside_output_dir(self, tmp_path: Path):
        src = tmp_path / "IMG_001.cr2"
        src.write_bytes(b"fake")
        out_dir = tmp_path / "out"
        with (
            patch("pyimgtag.raw_converter.shutil.which", return_value="/usr/bin/exiftool"),
            patch("pyimgtag.raw_converter.subprocess.run", return_value=self._mock_proc()),
        ):
            result = extract_raw_thumbnail(src, out_dir)
        assert result.parent == out_dir

    def test_stem_is_input_stem_thumb(self, tmp_path: Path):
        src = tmp_path / "IMG_001.cr2"
        src.write_bytes(b"fake")
        with (
            patch("pyimgtag.raw_converter.shutil.which", return_value="/usr/bin/exiftool"),
            patch("pyimgtag.raw_converter.subprocess.run", return_value=self._mock_proc()),
        ):
            result = extract_raw_thumbnail(src, tmp_path)
        assert result.stem == "IMG_001_thumb"

    def test_writes_stdout_bytes_to_disk(self, tmp_path: Path):
        src = tmp_path / "IMG_001.cr2"
        src.write_bytes(b"fake")
        with (
            patch("pyimgtag.raw_converter.shutil.which", return_value="/usr/bin/exiftool"),
            patch("pyimgtag.raw_converter.subprocess.run", return_value=self._mock_proc()),
        ):
            result = extract_raw_thumbnail(src, tmp_path)
        assert result.read_bytes() == self._FAKE_JPEG

    def test_raises_runtime_error_when_exiftool_missing(self, tmp_path: Path):
        src = tmp_path / "IMG_001.cr2"
        src.write_bytes(b"fake")
        with patch("pyimgtag.raw_converter.shutil.which", return_value=None):
            with pytest.raises(RuntimeError, match="exiftool"):
                extract_raw_thumbnail(src, tmp_path)

    def test_raises_file_not_found_when_input_missing(self, tmp_path: Path):
        src = tmp_path / "nonexistent.cr2"
        with patch("pyimgtag.raw_converter.shutil.which", return_value="/usr/bin/exiftool"):
            with pytest.raises(FileNotFoundError):
                extract_raw_thumbnail(src, tmp_path)

    def test_first_subprocess_call_uses_jpg_from_raw(self, tmp_path: Path):
        src = tmp_path / "IMG_001.cr2"
        src.write_bytes(b"fake")
        with (
            patch("pyimgtag.raw_converter.shutil.which", return_value="/usr/bin/exiftool"),
            patch(
                "pyimgtag.raw_converter.subprocess.run", return_value=self._mock_proc()
            ) as mock_run,
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
            patch("pyimgtag.raw_converter.shutil.which", return_value="/usr/bin/exiftool"),
            patch("pyimgtag.raw_converter.subprocess.run", side_effect=side_effects) as mock_run,
        ):
            extract_raw_thumbnail(src, tmp_path)
        second_call_args = mock_run.call_args_list[1][0][0]
        assert "-PreviewImage" in second_call_args

    def test_raises_no_embedded_jpeg_when_all_tags_fail(self, tmp_path: Path):
        src = tmp_path / "IMG_001.cr2"
        src.write_bytes(b"fake")
        empty_proc = self._mock_proc(returncode=0, stdout=b"")
        with (
            patch("pyimgtag.raw_converter.shutil.which", return_value="/usr/bin/exiftool"),
            patch("pyimgtag.raw_converter.subprocess.run", return_value=empty_proc),
        ):
            with pytest.raises(RuntimeError, match="No embedded JPEG"):
                extract_raw_thumbnail(src, tmp_path)

    def test_creates_output_dir_including_parents(self, tmp_path: Path):
        src = tmp_path / "IMG_001.cr2"
        src.write_bytes(b"fake")
        out_dir = tmp_path / "a" / "b" / "c"
        with (
            patch("pyimgtag.raw_converter.shutil.which", return_value="/usr/bin/exiftool"),
            patch("pyimgtag.raw_converter.subprocess.run", return_value=self._mock_proc()),
        ):
            extract_raw_thumbnail(src, out_dir)
        assert out_dir.is_dir()

    def test_uses_temp_dir_when_output_dir_none(self, tmp_path: Path):
        src = tmp_path / "IMG_001.cr2"
        src.write_bytes(b"fake")
        with (
            patch("pyimgtag.raw_converter.shutil.which", return_value="/usr/bin/exiftool"),
            patch("pyimgtag.raw_converter.subprocess.run", return_value=self._mock_proc()),
        ):
            result = extract_raw_thumbnail(src, None)
        try:
            assert result.exists()
            assert result.suffix == ".jpg"
        finally:
            shutil.rmtree(result.parent, ignore_errors=True)

    def test_falls_back_to_thumbnailimage_when_first_two_empty(self, tmp_path):
        call_count = [0]

        def fake_run(args, **kwargs):
            call_count[0] += 1
            if call_count[0] < 3:
                return self._mock_proc(stdout=b"")
            return self._mock_proc()

        with patch("pyimgtag.raw_converter.subprocess.run", side_effect=fake_run):
            with patch("pyimgtag.raw_converter.shutil.which", return_value="/usr/bin/exiftool"):
                src = tmp_path / "photo.cr2"
                src.write_bytes(b"fake")
                result = extract_raw_thumbnail(src, output_dir=tmp_path)

        assert result.exists()
        assert call_count[0] == 3


class TestRawpyAvailable:
    def test_returns_bool(self):
        result = rawpy_available()
        assert isinstance(result, bool)

    def test_returns_false_when_rawpy_not_importable(self):
        with patch("pyimgtag.raw_converter.rawpy", None):
            assert rawpy_available() is False


class TestConvertRawWithRawpy:
    def test_raises_when_rawpy_not_installed(self, tmp_path):
        with patch("pyimgtag.raw_converter.rawpy_available", return_value=False):
            with pytest.raises(RuntimeError, match="rawpy is not installed"):
                convert_raw_with_rawpy("photo.cr2", output_dir=tmp_path)

    def test_raises_when_input_not_found(self, tmp_path):
        with patch("pyimgtag.raw_converter.rawpy_available", return_value=True):
            with pytest.raises(FileNotFoundError):
                convert_raw_with_rawpy(tmp_path / "nonexistent.cr2", output_dir=tmp_path)

    def test_output_stem_is_raw(self, tmp_path):
        import numpy as np

        fake_cr2 = tmp_path / "IMG_001.cr2"
        fake_cr2.write_bytes(b"\x00" * 16)
        mock_rgb_array = np.zeros((10, 10, 3), dtype=np.uint8)
        mock_raw = MagicMock()
        mock_raw.__enter__ = MagicMock(return_value=mock_raw)
        mock_raw.__exit__ = MagicMock(return_value=False)
        mock_raw.postprocess.return_value = mock_rgb_array
        mock_rawpy = MagicMock()
        mock_rawpy.imread.return_value = mock_raw
        with patch("pyimgtag.raw_converter.rawpy_available", return_value=True):
            with patch.dict(sys.modules, {"rawpy": mock_rawpy}):
                result = convert_raw_with_rawpy(fake_cr2, output_dir=tmp_path)
        assert result.stem == "IMG_001_raw"
        assert result.suffix == ".jpg"

    def test_output_file_is_written(self, tmp_path):
        import numpy as np

        fake_cr2 = tmp_path / "photo.cr2"
        fake_cr2.write_bytes(b"\x00" * 16)
        mock_rgb_array = np.zeros((10, 10, 3), dtype=np.uint8)
        mock_raw = MagicMock()
        mock_raw.__enter__ = MagicMock(return_value=mock_raw)
        mock_raw.__exit__ = MagicMock(return_value=False)
        mock_raw.postprocess.return_value = mock_rgb_array
        mock_rawpy = MagicMock()
        mock_rawpy.imread.return_value = mock_raw
        with patch("pyimgtag.raw_converter.rawpy_available", return_value=True):
            with patch.dict(sys.modules, {"rawpy": mock_rawpy}):
                result = convert_raw_with_rawpy(fake_cr2, output_dir=tmp_path)
        assert result.exists()
        assert result.stat().st_size > 0

    def test_uses_temp_dir_when_output_dir_none(self, tmp_path):
        """output_dir=None must create an owned temp dir (lines 153-154)."""
        import numpy as np

        fake_cr2 = tmp_path / "IMG_X.cr2"
        fake_cr2.write_bytes(b"\x00" * 16)
        mock_rgb_array = np.zeros((8, 8, 3), dtype=np.uint8)
        mock_raw = MagicMock()
        mock_raw.__enter__ = MagicMock(return_value=mock_raw)
        mock_raw.__exit__ = MagicMock(return_value=False)
        mock_raw.postprocess.return_value = mock_rgb_array
        mock_rawpy = MagicMock()
        mock_rawpy.imread.return_value = mock_raw
        with patch("pyimgtag.raw_converter.rawpy_available", return_value=True):
            with patch.dict(sys.modules, {"rawpy": mock_rawpy}):
                result = convert_raw_with_rawpy(fake_cr2, output_dir=None)
        try:
            assert result.exists()
            assert "pyimgtag_raw_" in str(result.parent)
            assert result.stem == "IMG_X_raw"
        finally:
            shutil.rmtree(result.parent, ignore_errors=True)

    def test_postprocess_failure_cleans_up_owned_temp_dir(self, tmp_path):
        """A failure during conversion must rmtree the owned temp dir (170-173)."""
        fake_cr2 = tmp_path / "photo.cr2"
        fake_cr2.write_bytes(b"\x00" * 16)
        mock_rawpy = MagicMock()
        mock_rawpy.imread.side_effect = RuntimeError("decode boom")

        cleaned: list[str] = []

        def fake_rmtree(path, **kwargs):
            cleaned.append(str(path))

        with (
            patch("pyimgtag.raw_converter.rawpy_available", return_value=True),
            patch.dict(sys.modules, {"rawpy": mock_rawpy}),
            patch("pyimgtag.raw_converter.shutil.rmtree", side_effect=fake_rmtree),
            patch(
                "pyimgtag.raw_converter.tempfile.mkdtemp",
                return_value=str(tmp_path / "pyimgtag_raw_owned"),
            ),
        ):
            with pytest.raises(RuntimeError, match="decode boom"):
                convert_raw_with_rawpy(fake_cr2, output_dir=None)

        assert any("pyimgtag_raw_owned" in p for p in cleaned)

    def test_failure_with_explicit_output_dir_does_not_cleanup(self, tmp_path):
        """When the caller owns output_dir, a failure must NOT rmtree it."""
        fake_cr2 = tmp_path / "photo.cr2"
        fake_cr2.write_bytes(b"\x00" * 16)
        mock_rawpy = MagicMock()
        mock_rawpy.imread.side_effect = RuntimeError("decode boom")

        cleaned: list[str] = []

        with (
            patch("pyimgtag.raw_converter.rawpy_available", return_value=True),
            patch.dict(sys.modules, {"rawpy": mock_rawpy}),
            patch(
                "pyimgtag.raw_converter.shutil.rmtree",
                side_effect=lambda p, **k: cleaned.append(str(p)),
            ),
        ):
            with pytest.raises(RuntimeError, match="decode boom"):
                convert_raw_with_rawpy(fake_cr2, output_dir=tmp_path)

        assert cleaned == []


class TestRawpyImportFallback:
    """Cover the module-level ``except ImportError: rawpy = None`` fallback."""

    def test_import_without_rawpy_sets_rawpy_none(self):
        """Re-import raw_converter with rawpy hidden so lines 12-13 execute."""
        saved_mod = sys.modules.pop("pyimgtag.raw_converter", None)
        saved_rawpy = sys.modules.get("rawpy")
        try:
            with patch.dict(sys.modules, {"rawpy": None}):
                mod = importlib.import_module("pyimgtag.raw_converter")
                mod = importlib.reload(mod)
                assert mod.rawpy is None
                assert mod.rawpy_available() is False
        finally:
            # Restore the real module so other tests see rawpy as installed.
            sys.modules.pop("pyimgtag.raw_converter", None)
            if saved_rawpy is not None:
                sys.modules["rawpy"] = saved_rawpy
            importlib.import_module("pyimgtag.raw_converter")
            if saved_mod is not None:
                sys.modules["pyimgtag.raw_converter"] = saved_mod


class TestExtractRawThumbnailErrorPaths:
    _FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 200

    def test_timeout_in_loop_continues_to_next_tag(self, tmp_path: Path):
        import subprocess as _subprocess

        src = tmp_path / "photo.cr2"
        src.write_bytes(b"fake")

        call_count = [0]

        def fake_run(args, **kwargs):
            call_count[0] += 1
            if call_count[0] < 3:
                raise _subprocess.TimeoutExpired(cmd="exiftool", timeout=30)
            proc = MagicMock()
            proc.returncode = 0
            proc.stdout = self._FAKE_JPEG
            return proc

        with (
            patch("pyimgtag.raw_converter.shutil.which", return_value="/usr/bin/exiftool"),
            patch("pyimgtag.raw_converter.subprocess.run", side_effect=fake_run),
        ):
            result = extract_raw_thumbnail(src, output_dir=tmp_path)

        assert result.exists()
        assert call_count[0] == 3

    def test_nonzero_returncode_continues_to_next_tag(self, tmp_path: Path):
        src = tmp_path / "photo.cr2"
        src.write_bytes(b"fake")

        call_count = [0]

        def fake_run(args, **kwargs):
            call_count[0] += 1
            proc = MagicMock()
            if call_count[0] < 3:
                proc.returncode = 1
                proc.stdout = b""
            else:
                proc.returncode = 0
                proc.stdout = self._FAKE_JPEG
            return proc

        with (
            patch("pyimgtag.raw_converter.shutil.which", return_value="/usr/bin/exiftool"),
            patch("pyimgtag.raw_converter.subprocess.run", side_effect=fake_run),
        ):
            result = extract_raw_thumbnail(src, output_dir=tmp_path)

        assert result.exists()
        assert call_count[0] == 3

    def test_empty_stdout_continues_to_next_tag(self, tmp_path: Path):
        src = tmp_path / "photo.cr2"
        src.write_bytes(b"fake")

        responses = [
            MagicMock(returncode=0, stdout=b""),
            MagicMock(returncode=0, stdout=b""),
            MagicMock(returncode=0, stdout=self._FAKE_JPEG),
        ]

        with (
            patch("pyimgtag.raw_converter.shutil.which", return_value="/usr/bin/exiftool"),
            patch("pyimgtag.raw_converter.subprocess.run", side_effect=responses),
        ):
            result = extract_raw_thumbnail(src, output_dir=tmp_path)

        assert result.exists()

    def test_timeout_in_main_path_cleans_up_temp_dir(self, tmp_path: Path):
        import subprocess as _subprocess

        src = tmp_path / "photo.cr2"
        src.write_bytes(b"fake")

        cleaned = []

        def fake_rmtree(path, **kwargs):
            cleaned.append(str(path))

        with (
            patch("pyimgtag.raw_converter.shutil.which", return_value="/usr/bin/exiftool"),
            patch(
                "pyimgtag.raw_converter.subprocess.run",
                side_effect=_subprocess.TimeoutExpired(cmd="exiftool", timeout=30),
            ),
            patch("pyimgtag.raw_converter.shutil.rmtree", side_effect=fake_rmtree),
            patch(
                "pyimgtag.raw_converter.tempfile.mkdtemp",
                return_value=str(tmp_path / "pyimgtag_raw_tmp"),
            ),
        ):
            with pytest.raises(RuntimeError, match="No embedded JPEG"):
                extract_raw_thumbnail(src, output_dir=None)

        assert any("pyimgtag_raw_tmp" in p for p in cleaned)
