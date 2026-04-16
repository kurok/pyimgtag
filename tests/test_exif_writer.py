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
    def _patch_run(self, *side_effects):
        """Helper: patch subprocess.run with sequential return values."""
        return patch(
            "pyimgtag.exif_writer.subprocess.run",
            side_effect=list(side_effects),
        )

    def _date_read_result(self, dates=None):
        """Return a CompletedProcess for the date-reading exiftool call."""
        import json

        info = dates or {}
        return _make_completed_process(0, stdout=json.dumps([info]))

    def test_success_returns_none(self):
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=True):
            with self._patch_run(
                self._date_read_result(),
                _make_completed_process(0),
            ) as mock_run:
                result = write_exif_description(
                    "/path/photo.jpg", description="A sunset", keywords=["sunset", "beach"]
                )
                assert result is None
                # Second call is the write
                cmd = mock_run.call_args_list[1][0][0]
                assert "-ImageDescription=A sunset" in cmd
                assert "-XMP:Description=A sunset" in cmd
                assert "-IPTC:Caption-Abstract=A sunset" in cmd
                assert "-UserComment=A sunset" in cmd
                assert "-IPTC:Keywords=sunset" in cmd
                assert "-XMP:Subject=sunset" in cmd
                assert "-overwrite_original" in cmd

    def test_writes_xpkeywords(self):
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=True):
            with self._patch_run(
                self._date_read_result(),
                _make_completed_process(0),
            ) as mock_run:
                write_exif_description("/path/photo.jpg", keywords=["tag1", "tag2"])
                cmd = mock_run.call_args_list[1][0][0]
                assert "-XPKeywords=tag1;tag2" in cmd

    def test_description_only(self):
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=True):
            with self._patch_run(
                self._date_read_result(),
                _make_completed_process(0),
            ) as mock_run:
                result = write_exif_description("/path/photo.jpg", description="A sunset")
                assert result is None
                cmd = mock_run.call_args_list[1][0][0]
                assert "-ImageDescription=A sunset" in cmd
                # No keyword args
                kw_args = [a for a in cmd if "Keywords" in a and a != "-XPKeywords="]
                assert not kw_args or all("=" not in a.split("Keywords")[1] for a in kw_args)

    def test_keywords_only(self):
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=True):
            with self._patch_run(
                self._date_read_result(),
                _make_completed_process(0),
            ) as mock_run:
                result = write_exif_description("/path/photo.jpg", keywords=["tag1", "tag2"])
                assert result is None
                cmd = mock_run.call_args_list[1][0][0]
                desc_args = [a for a in cmd if a.startswith("-ImageDescription=")]
                assert not desc_args

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
            with self._patch_run(
                self._date_read_result(),
                _make_completed_process(1, stderr="File not found"),
            ):
                result = write_exif_description("/path/photo.jpg", description="test")
                assert result is not None
                assert "File not found" in result

    def test_nonzero_exit_no_stderr(self):
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=True):
            with self._patch_run(
                self._date_read_result(),
                _make_completed_process(1, stderr=""),
            ):
                result = write_exif_description("/path/photo.jpg", description="test")
                assert result is not None
                assert "1" in result

    def test_timeout(self):
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=True):
            with self._patch_run(
                self._date_read_result(),
                subprocess.TimeoutExpired(cmd="exiftool", timeout=30),
            ):
                result = write_exif_description("/path/photo.jpg", description="test")
                assert result is not None
                assert "timed out" in result.lower()

    def test_oserror(self):
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=True):
            with self._patch_run(
                self._date_read_result(),
                OSError("No such file"),
            ):
                result = write_exif_description("/path/photo.jpg", description="test")
                assert result is not None
                assert "No such file" in result

    def test_keywords_clears_existing_first(self):
        """Should clear IPTC:Keywords, XMP:Subject, and XPKeywords before setting new ones."""
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=True):
            with self._patch_run(
                self._date_read_result(),
                _make_completed_process(0),
            ) as mock_run:
                write_exif_description("/path/photo.jpg", keywords=["new"])
                cmd = mock_run.call_args_list[1][0][0]
                # Clear args should come before set args
                clear_kw_idx = cmd.index("-IPTC:Keywords=")
                set_kw_idx = cmd.index("-IPTC:Keywords=new")
                assert clear_kw_idx < set_kw_idx

    def test_file_path_is_last_arg(self):
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=True):
            with self._patch_run(
                self._date_read_result(),
                _make_completed_process(0),
            ) as mock_run:
                write_exif_description("/path/photo.jpg", description="test")
                cmd = mock_run.call_args_list[1][0][0]
                assert cmd[-1] == "/path/photo.jpg"

    def test_preserves_date_fields(self):
        """Date fields read before write should be restored in the write command."""
        dates = {"DateTimeOriginal": "2026:04:01 10:30:00", "CreateDate": "2026:04:01 10:30:00"}
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=True):
            with self._patch_run(
                self._date_read_result(dates),
                _make_completed_process(0),
            ) as mock_run:
                write_exif_description("/path/photo.jpg", description="test")
                cmd = mock_run.call_args_list[1][0][0]
                assert "-DateTimeOriginal=2026:04:01 10:30:00" in cmd
                assert "-CreateDate=2026:04:01 10:30:00" in cmd

    def test_date_read_failure_does_not_block_write(self):
        """If date reading fails, the write should still proceed (just without date restoration)."""
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=True):
            with self._patch_run(
                _make_completed_process(1),  # date read fails
                _make_completed_process(0),  # write succeeds
            ):
                result = write_exif_description("/path/photo.jpg", description="test")
                assert result is None

    def test_writes_user_comment(self):
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=True):
            with self._patch_run(
                self._date_read_result(),
                _make_completed_process(0),
            ) as mock_run:
                write_exif_description("/path/photo.jpg", description="A sunset photo")
                cmd = mock_run.call_args_list[1][0][0]
                assert "-UserComment=A sunset photo" in cmd
