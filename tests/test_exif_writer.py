"""Unit tests for the exif_writer module."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

from pyimgtag.exif_writer import is_exiftool_available, write_exif_description


def _make_completed_process(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    mock = MagicMock(spec=subprocess.CompletedProcess)
    mock.returncode = returncode
    mock.stdout = stdout
    mock.stderr = stderr
    return mock


class TestIsExiftoolAvailable:
    def test_returns_true_when_found(self):
        with patch("pyimgtag.exif_writer.shutil.which", return_value="/usr/local/bin/exiftool"):
            assert is_exiftool_available() is True

    def test_returns_false_when_not_found(self):
        with patch("pyimgtag.exif_writer.shutil.which", return_value=None):
            assert is_exiftool_available() is False


class TestWriteExifDescription:
    def test_success_returns_none(self):
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=True):
            with patch(
                "pyimgtag.exif_writer.subprocess.run",
                return_value=_make_completed_process(0),
            ) as mock_run:
                result = write_exif_description(
                    "/path/photo.jpg", description="A sunset", keywords=["sunset", "beach"]
                )
                assert result is None
                cmd = mock_run.call_args[0][0]
                assert "-ImageDescription=A sunset" in cmd
                assert "-XMP:Description=A sunset" in cmd
                assert "-IPTC:Caption-Abstract=A sunset" in cmd
                assert "-IPTC:Keywords=sunset" in cmd
                assert "-XMP:Subject=sunset" in cmd
                assert "-IPTC:Keywords=beach" in cmd
                assert "-overwrite_original" in cmd

    def test_description_only(self):
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=True):
            with patch(
                "pyimgtag.exif_writer.subprocess.run",
                return_value=_make_completed_process(0),
            ) as mock_run:
                result = write_exif_description("/path/photo.jpg", description="A sunset")
                assert result is None
                cmd = mock_run.call_args[0][0]
                assert "-ImageDescription=A sunset" in cmd
                assert "-IPTC:Keywords=" not in cmd

    def test_keywords_only(self):
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=True):
            with patch(
                "pyimgtag.exif_writer.subprocess.run",
                return_value=_make_completed_process(0),
            ) as mock_run:
                result = write_exif_description("/path/photo.jpg", keywords=["tag1", "tag2"])
                assert result is None
                cmd = mock_run.call_args[0][0]
                assert "-ImageDescription=" not in " ".join(cmd)

    def test_nothing_to_write_returns_none(self):
        result = write_exif_description("/path/photo.jpg")
        assert result is None

    def test_exiftool_not_available(self):
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=False):
            result = write_exif_description("/path/photo.jpg", description="test")
            assert result is not None
            assert "exiftool" in result.lower()

    def test_nonzero_exit_with_stderr(self):
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=True):
            with patch(
                "pyimgtag.exif_writer.subprocess.run",
                return_value=_make_completed_process(1, stderr="File not found"),
            ):
                result = write_exif_description("/path/photo.jpg", description="test")
                assert result is not None
                assert "File not found" in result

    def test_nonzero_exit_no_stderr(self):
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=True):
            with patch(
                "pyimgtag.exif_writer.subprocess.run",
                return_value=_make_completed_process(1, stderr=""),
            ):
                result = write_exif_description("/path/photo.jpg", description="test")
                assert result is not None
                assert "1" in result

    def test_timeout(self):
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=True):
            with patch(
                "pyimgtag.exif_writer.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="exiftool", timeout=30),
            ):
                result = write_exif_description("/path/photo.jpg", description="test")
                assert result is not None
                assert "timed out" in result.lower()

    def test_oserror(self):
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=True):
            with patch(
                "pyimgtag.exif_writer.subprocess.run",
                side_effect=OSError("No such file"),
            ):
                result = write_exif_description("/path/photo.jpg", description="test")
                assert result is not None
                assert "No such file" in result

    def test_keywords_clears_existing_first(self):
        """Should clear IPTC:Keywords and XMP:Subject before setting new ones."""
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=True):
            with patch(
                "pyimgtag.exif_writer.subprocess.run",
                return_value=_make_completed_process(0),
            ) as mock_run:
                write_exif_description("/path/photo.jpg", keywords=["new"])
                cmd = mock_run.call_args[0][0]
                # Clear args should come before set args
                clear_kw_idx = cmd.index("-IPTC:Keywords=")
                set_kw_idx = cmd.index("-IPTC:Keywords=new")
                assert clear_kw_idx < set_kw_idx

    def test_file_path_is_last_arg(self):
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=True):
            with patch(
                "pyimgtag.exif_writer.subprocess.run",
                return_value=_make_completed_process(0),
            ) as mock_run:
                write_exif_description("/path/photo.jpg", description="test")
                cmd = mock_run.call_args[0][0]
                assert cmd[-1] == "/path/photo.jpg"
