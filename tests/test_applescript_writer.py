"""Unit tests for the applescript_writer module."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

from pyimgtag.applescript_writer import (
    _build_applescript,
    _escape_applescript_string,
    _write_via_photoscript,
    is_applescript_available,
    read_keywords_from_photos,
    write_to_photos,
)

# ---------------------------------------------------------------------------
# _escape_applescript_string
# ---------------------------------------------------------------------------


class TestEscapeApplescriptString:
    def test_plain_string_unchanged(self):
        assert _escape_applescript_string("hello world") == "hello world"

    def test_double_quote_escaped(self):
        result = _escape_applescript_string('say "hello"')
        assert '\\"' in result
        assert '"' not in result.replace('\\"', "")

    def test_backslash_escaped(self):
        result = _escape_applescript_string("path\\to\\file")
        assert "\\\\" in result

    def test_newline_replaced_with_space(self):
        result = _escape_applescript_string("line1\nline2")
        assert "\n" not in result
        assert "line1 line2" == result

    def test_crlf_replaced_with_space(self):
        result = _escape_applescript_string("line1\r\nline2")
        assert "\r" not in result
        assert "\n" not in result

    def test_carriage_return_replaced_with_space(self):
        result = _escape_applescript_string("line1\rline2")
        assert "\r" not in result

    def test_backslash_before_quote(self):
        # backslash followed by a quote: both must be escaped
        result = _escape_applescript_string('a\\"b')
        assert result == 'a\\\\\\"b'


# ---------------------------------------------------------------------------
# _build_applescript
# ---------------------------------------------------------------------------


class TestBuildApplescript:
    def test_uses_media_item_id_lookup(self):
        script = _build_applescript("photo.jpg", ["sunset"], None)
        assert 'media item id "photo"' in script

    def test_uuid_is_filename_stem(self):
        script = _build_applescript("AABB-1234.heic", ["tag"], None)
        assert 'media item id "AABB-1234"' in script

    def test_contains_tags_list(self):
        script = _build_applescript("photo.jpg", ["beach", "sunset"], None)
        assert '{"beach", "sunset"}' in script

    def test_single_tag(self):
        script = _build_applescript("img.jpg", ["nature"], None)
        assert '{"nature"}' in script

    def test_description_present_when_summary_given(self):
        script = _build_applescript("img.jpg", ["tag"], "A nice shot")
        assert 'set description of theItem to "A nice shot"' in script

    def test_description_absent_when_summary_none(self):
        script = _build_applescript("img.jpg", ["tag"], None)
        assert "set description" not in script

    def test_filename_with_quotes_escaped(self):
        script = _build_applescript('say "hi".jpg', ["tag"], None)
        assert '\\"' in script

    def test_tag_with_quotes_escaped(self):
        script = _build_applescript("photo.jpg", ['O"Brien'], None)
        assert '\\"' in script

    def test_summary_with_quotes_escaped(self):
        script = _build_applescript("photo.jpg", ["tag"], 'Caption "quoted"')
        assert '\\"' in script

    def test_tell_application_photos_block(self):
        script = _build_applescript("photo.jpg", ["a"], None)
        assert 'tell application "Photos"' in script
        assert "end tell" in script

    def test_title_present_when_given(self):
        script = _build_applescript("img.jpg", ["tag"], None, title="Sunset at beach")
        assert 'set name of theItem to "Sunset at beach"' in script

    def test_title_absent_when_none(self):
        script = _build_applescript("img.jpg", ["tag"], None, title=None)
        assert "set name" not in script

    def test_title_with_quotes_escaped(self):
        script = _build_applescript("img.jpg", ["tag"], None, title='A "great" shot')
        assert '\\"great\\"' in script


# ---------------------------------------------------------------------------
# is_applescript_available
# ---------------------------------------------------------------------------


class TestIsApplescriptAvailable:
    def test_returns_true_when_osascript_found(self):
        with patch("pyimgtag.applescript_writer.shutil.which", return_value="/usr/bin/osascript"):
            with patch("pyimgtag.applescript_writer._IS_MACOS", True):
                assert is_applescript_available() is True

    def test_returns_false_when_osascript_not_found(self):
        with patch("pyimgtag.applescript_writer.shutil.which", return_value=None):
            with patch("pyimgtag.applescript_writer._IS_MACOS", True):
                assert is_applescript_available() is False

    def test_returns_false_on_non_macos(self):
        # Should return False on non-macOS even if osascript exists
        with patch("pyimgtag.applescript_writer._IS_MACOS", False):
            with patch(
                "pyimgtag.applescript_writer.shutil.which", return_value="/usr/bin/osascript"
            ):
                assert is_applescript_available() is False


# ---------------------------------------------------------------------------
# write_to_photos — success and failure cases
# ---------------------------------------------------------------------------


def _make_completed_process(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    mock = MagicMock(spec=subprocess.CompletedProcess)
    mock.returncode = returncode
    mock.stdout = stdout
    mock.stderr = stderr
    return mock


@patch("pyimgtag.applescript_writer._IS_MACOS", True)
@patch("pyimgtag.applescript_writer._HAS_PHOTOSCRIPT", False)
class TestWriteToPhotos:
    # Tests for the osascript fallback path (photoscript disabled).
    # Patch both is_applescript_available (True) and subprocess.run for all tests
    # that test the actual write path.

    def test_success_returns_none(self):
        with patch("pyimgtag.applescript_writer.is_applescript_available", return_value=True):
            with patch(
                "pyimgtag.applescript_writer.subprocess.run",
                return_value=_make_completed_process(0),
            ):
                result = write_to_photos("/path/to/photo.jpg", ["beach", "sunset"], "Nice photo")
                assert result is None

    def test_success_without_summary(self):
        with patch("pyimgtag.applescript_writer.is_applescript_available", return_value=True):
            with patch(
                "pyimgtag.applescript_writer.subprocess.run",
                return_value=_make_completed_process(0),
            ):
                result = write_to_photos("/path/to/photo.jpg", ["beach"], None)
                assert result is None

    def test_failure_nonzero_exit_with_stderr(self):
        with patch("pyimgtag.applescript_writer.is_applescript_available", return_value=True):
            with patch(
                "pyimgtag.applescript_writer.subprocess.run",
                return_value=_make_completed_process(1, stderr="Photos is not running."),
            ):
                result = write_to_photos("/path/to/photo.jpg", ["tag"], None)
                assert result is not None
                assert "Photos is not running." in result

    def test_failure_nonzero_exit_no_stderr(self):
        with patch("pyimgtag.applescript_writer.is_applescript_available", return_value=True):
            with patch(
                "pyimgtag.applescript_writer.subprocess.run",
                return_value=_make_completed_process(1, stderr=""),
            ):
                result = write_to_photos("/path/to/photo.jpg", ["tag"], None)
                assert result is not None
                assert "1" in result  # exit code mentioned

    def test_returns_error_on_non_macos(self):
        # write_to_photos should gracefully fail on non-macOS
        with patch("pyimgtag.applescript_writer._IS_MACOS", False):
            result = write_to_photos("/path/photo.jpg", ["tag"], None)
            assert result is not None
            assert "macOS" in result

    def test_osascript_not_available(self):
        with patch("pyimgtag.applescript_writer.is_applescript_available", return_value=False):
            result = write_to_photos("/path/photo.jpg", ["tag"], None)
            assert result is not None
            assert "osascript" in result.lower()

    def test_timeout_returns_error(self):
        with patch("pyimgtag.applescript_writer.is_applescript_available", return_value=True):
            with patch(
                "pyimgtag.applescript_writer.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="osascript", timeout=30),
            ):
                result = write_to_photos("/path/photo.jpg", ["tag"], None)
                assert result is not None
                assert "timed out" in result.lower()

    def test_oserror_returns_error(self):
        with patch("pyimgtag.applescript_writer.is_applescript_available", return_value=True):
            with patch(
                "pyimgtag.applescript_writer.subprocess.run",
                side_effect=OSError("No such file"),
            ):
                result = write_to_photos("/path/photo.jpg", ["tag"], None)
                assert result is not None
                assert "No such file" in result

    def test_uses_basename_not_full_path(self):
        """The script sent to osascript must use just the filename, not the full path."""
        captured: list[str] = []

        def fake_run(cmd: list[str], **kwargs):  # noqa: ANN001
            # cmd = ["/usr/bin/osascript", "-e", <script>]
            captured.append(cmd[2])
            return _make_completed_process(0)

        with patch("pyimgtag.applescript_writer.is_applescript_available", return_value=True):
            with patch("pyimgtag.applescript_writer.subprocess.run", side_effect=fake_run):
                write_to_photos("/some/deep/path/vacation.jpg", ["sun"], None)

        assert captured, "subprocess.run was not called"
        script = captured[0]
        assert 'media item id "vacation"' in script
        assert "/some/deep/path/" not in script

    def test_tags_formatted_as_applescript_list(self):
        """Tags must appear as an AppleScript list {"a", "b", "c"}."""
        captured: list[str] = []

        def fake_run(cmd: list[str], **kwargs):  # noqa: ANN001
            captured.append(cmd[2])
            return _make_completed_process(0)

        with patch("pyimgtag.applescript_writer.is_applescript_available", return_value=True):
            with patch("pyimgtag.applescript_writer.subprocess.run", side_effect=fake_run):
                write_to_photos("/path/photo.jpg", ["alpha", "beta", "gamma"], None)

        script = captured[0]
        assert '{"alpha", "beta", "gamma"}' in script

    def test_summary_included_when_provided(self):
        """Description line must appear when summary is not None."""
        captured: list[str] = []

        def fake_run(cmd: list[str], **kwargs):  # noqa: ANN001
            captured.append(cmd[2])
            return _make_completed_process(0)

        with patch("pyimgtag.applescript_writer.is_applescript_available", return_value=True):
            with patch("pyimgtag.applescript_writer.subprocess.run", side_effect=fake_run):
                write_to_photos("/path/photo.jpg", ["tag"], "Beautiful sunset over the ocean")

        script = captured[0]
        assert "Beautiful sunset over the ocean" in script
        assert "set description" in script

    def test_title_included_when_provided(self):
        """Title line must appear when title is not None."""
        captured: list[str] = []

        def fake_run(cmd: list[str], **kwargs):  # noqa: ANN001
            captured.append(cmd[2])
            return _make_completed_process(0)

        with patch("pyimgtag.applescript_writer.is_applescript_available", return_value=True):
            with patch("pyimgtag.applescript_writer.subprocess.run", side_effect=fake_run):
                write_to_photos("/path/photo.jpg", ["tag"], None, title="Beach day")

        script = captured[0]
        assert 'set name of theItem to "Beach day"' in script

    def test_title_omitted_when_none(self):
        """Title line must not appear when title is None."""
        captured: list[str] = []

        def fake_run(cmd: list[str], **kwargs):  # noqa: ANN001
            captured.append(cmd[2])
            return _make_completed_process(0)

        with patch("pyimgtag.applescript_writer.is_applescript_available", return_value=True):
            with patch("pyimgtag.applescript_writer.subprocess.run", side_effect=fake_run):
                write_to_photos("/path/photo.jpg", ["tag"], None, title=None)

        script = captured[0]
        assert "set name" not in script

    def test_summary_omitted_when_none(self):
        """Description line must not appear when summary is None."""
        captured: list[str] = []

        def fake_run(cmd: list[str], **kwargs):  # noqa: ANN001
            captured.append(cmd[2])
            return _make_completed_process(0)

        with patch("pyimgtag.applescript_writer.is_applescript_available", return_value=True):
            with patch("pyimgtag.applescript_writer.subprocess.run", side_effect=fake_run):
                write_to_photos("/path/photo.jpg", ["tag"], None)

        script = captured[0]
        assert "set description" not in script

    def test_filename_with_spaces(self):
        """Filenames with spaces must be handled correctly."""
        captured: list[str] = []

        def fake_run(cmd: list[str], **kwargs):  # noqa: ANN001
            captured.append(cmd[2])
            return _make_completed_process(0)

        with patch("pyimgtag.applescript_writer.is_applescript_available", return_value=True):
            with patch("pyimgtag.applescript_writer.subprocess.run", side_effect=fake_run):
                write_to_photos("/path/my vacation photo.jpg", ["beach"], None)

        script = captured[0]
        assert 'media item id "my vacation photo"' in script

    def test_filename_with_quotes(self):
        """Filenames with double quotes must be escaped."""
        captured: list[str] = []

        def fake_run(cmd: list[str], **kwargs):  # noqa: ANN001
            captured.append(cmd[2])
            return _make_completed_process(0)

        with patch("pyimgtag.applescript_writer.is_applescript_available", return_value=True):
            with patch("pyimgtag.applescript_writer.subprocess.run", side_effect=fake_run):
                write_to_photos('/path/photo "hdr".jpg', ["tag"], None)

        script = captured[0]
        # The double quotes in the filename must be escaped
        assert '\\"hdr\\"' in script

    def test_filename_with_backslash(self):
        """Filenames with backslashes must be double-escaped."""
        captured: list[str] = []

        def fake_run(cmd: list[str], **kwargs):  # noqa: ANN001
            captured.append(cmd[2])
            return _make_completed_process(0)

        with patch("pyimgtag.applescript_writer.is_applescript_available", return_value=True):
            with patch("pyimgtag.applescript_writer.subprocess.run", side_effect=fake_run):
                write_to_photos("/path/photo\\backup.jpg", ["tag"], None)

        script = captured[0]
        assert "\\\\" in script


# ---------------------------------------------------------------------------
# photoscript backend
# ---------------------------------------------------------------------------


class TestWriteViaPhotoscript:
    def test_success_sets_keywords_and_description(self):
        mock_photo = MagicMock()
        mock_lib = MagicMock()
        mock_lib.photo.return_value = mock_photo

        with patch("pyimgtag.applescript_writer.photoscript") as mock_ps:
            mock_ps.PhotosLibrary.return_value = mock_lib
            result = _write_via_photoscript("photo.jpg", ["sunset", "beach"], "Nice photo")

        assert result is None
        assert mock_photo.keywords == ["sunset", "beach"]
        assert mock_photo.description == "Nice photo"

    def test_success_with_title(self):
        mock_photo = MagicMock()
        mock_lib = MagicMock()
        mock_lib.photo.return_value = mock_photo

        with patch("pyimgtag.applescript_writer.photoscript") as mock_ps:
            mock_ps.PhotosLibrary.return_value = mock_lib
            result = _write_via_photoscript("photo.jpg", ["tag"], None, title="My Title")

        assert result is None
        assert mock_photo.title == "My Title"

    def test_no_match_returns_error(self):
        mock_lib = MagicMock()
        mock_lib.photo.side_effect = Exception("photo not found")

        with patch("pyimgtag.applescript_writer.photoscript") as mock_ps:
            mock_ps.PhotosLibrary.return_value = mock_lib
            result = _write_via_photoscript("missing.jpg", ["tag"], None)

        assert result is not None
        assert "missing.jpg" in result

    def test_lookup_uses_uuid_from_filename_stem(self):
        mock_photo = MagicMock()
        mock_lib = MagicMock()
        mock_lib.photo.return_value = mock_photo

        with patch("pyimgtag.applescript_writer.photoscript") as mock_ps:
            mock_ps.PhotosLibrary.return_value = mock_lib
            result = _write_via_photoscript("AABBCCDD-1234.heic", ["tag"], None)

        assert result is None
        mock_lib.photo.assert_called_once_with(uuid="AABBCCDD-1234")

    def test_exception_returns_error(self):
        with patch("pyimgtag.applescript_writer.photoscript") as mock_ps:
            mock_ps.PhotosLibrary.side_effect = Exception("Photos not running")
            result = _write_via_photoscript("photo.jpg", ["tag"], None)

        assert result is not None
        assert "Photos not running" in result

    def test_skips_description_when_none(self):
        mock_photo = MagicMock()
        mock_photo.description = "original"
        mock_lib = MagicMock()
        mock_lib.photo.return_value = mock_photo

        with patch("pyimgtag.applescript_writer.photoscript") as mock_ps:
            mock_ps.PhotosLibrary.return_value = mock_lib
            _write_via_photoscript("photo.jpg", ["tag"], None)

        # description should not have been reassigned
        assert mock_photo.description == "original"


@patch("pyimgtag.applescript_writer._IS_MACOS", True)
class TestWriteToPhotosBackendSelection:
    def test_uses_photoscript_when_available(self):
        with patch("pyimgtag.applescript_writer._HAS_PHOTOSCRIPT", True):
            with patch(
                "pyimgtag.applescript_writer._write_via_photoscript", return_value=None
            ) as mock_ps:
                result = write_to_photos("/path/photo.jpg", ["tag"], "desc")
                assert result is None
                mock_ps.assert_called_once_with("photo.jpg", ["tag"], "desc", title=None)

    def test_falls_back_to_osascript_when_no_photoscript(self):
        with patch("pyimgtag.applescript_writer._HAS_PHOTOSCRIPT", False):
            with patch("pyimgtag.applescript_writer.is_applescript_available", return_value=True):
                with patch(
                    "pyimgtag.applescript_writer.subprocess.run",
                    return_value=_make_completed_process(0),
                ):
                    result = write_to_photos("/path/photo.jpg", ["tag"], None)
                    assert result is None

    def test_falls_back_to_osascript_when_photoscript_uuid_fails(self):
        with patch("pyimgtag.applescript_writer._HAS_PHOTOSCRIPT", True):
            with patch(
                "pyimgtag.applescript_writer._write_via_photoscript",
                return_value="No Photos item found with filename: photo.jpg",
            ):
                with patch(
                    "pyimgtag.applescript_writer.is_applescript_available",
                    return_value=True,
                ):
                    with patch(
                        "pyimgtag.applescript_writer.subprocess.run",
                        return_value=_make_completed_process(0),
                    ):
                        result = write_to_photos("/path/photo.jpg", ["tag"], None)
                        assert result is None


# ---------------------------------------------------------------------------
# read_keywords_from_photos
# ---------------------------------------------------------------------------


class TestReadKeywordsFromPhotos:
    def test_returns_list_from_osascript(self):
        with (
            patch("pyimgtag.applescript_writer._IS_MACOS", True),
            patch("pyimgtag.applescript_writer._HAS_PHOTOSCRIPT", False),
            patch("pyimgtag.applescript_writer.is_applescript_available", return_value=True),
            patch("pyimgtag.applescript_writer.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(
                returncode=0, stdout="sunset\nbeach\ntravel\n", stderr=""
            )
            result = read_keywords_from_photos("/Library/Photos/img.jpg")
        assert result == ["sunset", "beach", "travel"]

    def test_returns_none_on_not_macos(self):
        with patch("pyimgtag.applescript_writer._IS_MACOS", False):
            result = read_keywords_from_photos("/any/path.jpg")
        assert result is None

    def test_returns_none_on_osascript_error(self):
        with (
            patch("pyimgtag.applescript_writer._IS_MACOS", True),
            patch("pyimgtag.applescript_writer._HAS_PHOTOSCRIPT", False),
            patch("pyimgtag.applescript_writer.is_applescript_available", return_value=True),
            patch("pyimgtag.applescript_writer.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
            result = read_keywords_from_photos("/Library/Photos/img.jpg")
        assert result is None

    def test_returns_empty_list_when_no_keywords(self):
        with (
            patch("pyimgtag.applescript_writer._IS_MACOS", True),
            patch("pyimgtag.applescript_writer._HAS_PHOTOSCRIPT", False),
            patch("pyimgtag.applescript_writer.is_applescript_available", return_value=True),
            patch("pyimgtag.applescript_writer.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="\n", stderr="")
            result = read_keywords_from_photos("/Library/Photos/img.jpg")
        assert result == []

    def test_reads_via_photoscript_when_available(self):
        mock_photo = MagicMock()
        mock_photo.keywords = ["dog", "park"]
        mock_lib = MagicMock()
        mock_lib.photo.return_value = mock_photo

        with (
            patch("pyimgtag.applescript_writer._IS_MACOS", True),
            patch("pyimgtag.applescript_writer._HAS_PHOTOSCRIPT", True),
            patch("pyimgtag.applescript_writer.photoscript") as mock_ps,
        ):
            mock_ps.PhotosLibrary.return_value = mock_lib
            result = read_keywords_from_photos("/Library/Photos/img.jpg")
        assert result == ["dog", "park"]

    def test_returns_none_when_photoscript_photo_not_found(self):
        mock_lib = MagicMock()
        mock_lib.photo.side_effect = Exception("not found")

        with (
            patch("pyimgtag.applescript_writer._IS_MACOS", True),
            patch("pyimgtag.applescript_writer._HAS_PHOTOSCRIPT", True),
            patch("pyimgtag.applescript_writer.photoscript") as mock_ps,
        ):
            mock_ps.PhotosLibrary.return_value = mock_lib
            result = read_keywords_from_photos("/Library/Photos/img.jpg")
        assert result is None

    def test_returns_none_when_photoscript_library_raises(self):
        with (
            patch("pyimgtag.applescript_writer._IS_MACOS", True),
            patch("pyimgtag.applescript_writer._HAS_PHOTOSCRIPT", True),
            patch("pyimgtag.applescript_writer.photoscript") as mock_ps,
        ):
            mock_ps.PhotosLibrary.side_effect = Exception("Photos not running")
            result = read_keywords_from_photos("/Library/Photos/img.jpg")
        assert result is None

    def test_returns_none_when_osascript_unavailable(self):
        with (
            patch("pyimgtag.applescript_writer._IS_MACOS", True),
            patch("pyimgtag.applescript_writer._HAS_PHOTOSCRIPT", False),
            patch("pyimgtag.applescript_writer.is_applescript_available", return_value=False),
        ):
            result = read_keywords_from_photos("/Library/Photos/img.jpg")
        assert result is None

    def test_returns_none_on_osascript_timeout(self):
        with (
            patch("pyimgtag.applescript_writer._IS_MACOS", True),
            patch("pyimgtag.applescript_writer._HAS_PHOTOSCRIPT", False),
            patch("pyimgtag.applescript_writer.is_applescript_available", return_value=True),
            patch(
                "pyimgtag.applescript_writer.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="osascript", timeout=30),
            ),
        ):
            result = read_keywords_from_photos("/Library/Photos/img.jpg")
        assert result is None


# ---------------------------------------------------------------------------
# write_to_photos mode parameter
# ---------------------------------------------------------------------------


@patch("pyimgtag.applescript_writer._HAS_PHOTOSCRIPT", False)
class TestWriteToPhotosMode:
    def test_overwrite_mode_does_not_read_existing(self):
        """overwrite (default) calls write without reading existing keywords."""
        with (
            patch("pyimgtag.applescript_writer._IS_MACOS", True),
            patch("pyimgtag.applescript_writer._HAS_PHOTOSCRIPT", False),
            patch("pyimgtag.applescript_writer.read_keywords_from_photos") as mock_read,
            patch("pyimgtag.applescript_writer._write_via_osascript", return_value=None),
        ):
            write_to_photos("/path/img.jpg", ["new_tag"], None, mode="overwrite")
        mock_read.assert_not_called()

    def test_append_mode_merges_with_existing(self):
        """append mode reads existing keywords and merges new ones in."""
        with (
            patch("pyimgtag.applescript_writer._IS_MACOS", True),
            patch("pyimgtag.applescript_writer._HAS_PHOTOSCRIPT", False),
            patch(
                "pyimgtag.applescript_writer.read_keywords_from_photos",
                return_value=["existing", "score:3.5"],
            ),
            patch(
                "pyimgtag.applescript_writer._write_via_osascript", return_value=None
            ) as mock_write,
        ):
            write_to_photos("/path/img.jpg", ["new_tag", "score:4.2"], None, mode="append")
        called_tags = mock_write.call_args[0][1]
        assert "existing" in called_tags
        assert "score:4.2" in called_tags
        assert "new_tag" in called_tags
        assert "score:3.5" not in called_tags  # old score removed

    def test_append_mode_deduplicates(self):
        """append mode does not produce duplicate tags."""
        with (
            patch("pyimgtag.applescript_writer._IS_MACOS", True),
            patch("pyimgtag.applescript_writer._HAS_PHOTOSCRIPT", False),
            patch(
                "pyimgtag.applescript_writer.read_keywords_from_photos",
                return_value=["sunset", "beach"],
            ),
            patch(
                "pyimgtag.applescript_writer._write_via_osascript", return_value=None
            ) as mock_write,
        ):
            write_to_photos("/path/img.jpg", ["sunset", "travel"], None, mode="append")
        called_tags = mock_write.call_args[0][1]
        assert called_tags.count("sunset") == 1

    def test_default_mode_is_overwrite(self):
        """write_to_photos without mode= behaves as overwrite."""
        with (
            patch("pyimgtag.applescript_writer._IS_MACOS", True),
            patch("pyimgtag.applescript_writer._HAS_PHOTOSCRIPT", False),
            patch("pyimgtag.applescript_writer.read_keywords_from_photos") as mock_read,
            patch("pyimgtag.applescript_writer._write_via_osascript", return_value=None),
        ):
            write_to_photos("/path/img.jpg", ["tag"], None)
        mock_read.assert_not_called()

    def test_append_mode_aborts_when_read_returns_none(self):
        """append mode must return an error and not write when read returns None."""
        with (
            patch("pyimgtag.applescript_writer._IS_MACOS", True),
            patch("pyimgtag.applescript_writer._HAS_PHOTOSCRIPT", False),
            patch(
                "pyimgtag.applescript_writer.read_keywords_from_photos",
                return_value=None,
            ),
            patch(
                "pyimgtag.applescript_writer._write_via_osascript", return_value=None
            ) as mock_write,
        ):
            result = write_to_photos("/path/img.jpg", ["new_tag"], None, mode="append")
        assert result is not None
        assert "aborted" in result
        mock_write.assert_not_called()
