"""Unit tests for the exif_writer module."""

from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404
from unittest.mock import MagicMock, patch

import pytest

from pyimgtag.exif_writer import (
    RAW_SIDECAR_ONLY_EXTENSIONS,
    SUPPORTED_DIRECT_WRITE_EXTENSIONS,
    _read_date_fields,
    diff_metadata,
    is_exiftool_available,
    read_existing_metadata,
    write_exif_description,
    write_xmp_sidecar,
)


def _exiftool_json(path, *tags) -> dict:
    """Read tags back as JSON, decoded as UTF-8 rather than by locale.

    ``text=True`` decodes the pipe with the locale encoding, which is not
    UTF-8 on every runner; exiftool writes its JSON as UTF-8 everywhere.
    """
    proc = subprocess.run(  # noqa: S603  # nosec B603
        [shutil.which("exiftool"), "-json", *tags, str(path)],
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    return json.loads(proc.stdout.decode("utf-8"))[0]


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
            "pyimgtag.exif_writer._run_exiftool",
            side_effect=list(side_effects),
        )

    def _date_read_result(self, dates=None):
        """Return a CompletedProcess for the date-reading exiftool call."""
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


class TestWriteExifDescriptionFormats:
    """Tests for the fmt and merge parameters."""

    def _patch_run(self, *side_effects):
        return patch(
            "pyimgtag.exif_writer._run_exiftool",
            side_effect=list(side_effects),
        )

    def _date_read_result(self):
        return _make_completed_process(0, stdout=json.dumps([{}]))

    def test_fmt_xmp_writes_only_xmp_description(self):
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=True):
            with self._patch_run(self._date_read_result(), _make_completed_process(0)) as mock_run:
                write_exif_description("/p/photo.jpg", description="desc", fmt="xmp")
                cmd = mock_run.call_args_list[1][0][0]
                assert "-XMP:Description=desc" in cmd
                assert "-ImageDescription=desc" not in cmd
                assert "-IPTC:Caption-Abstract=desc" not in cmd

    def test_fmt_iptc_writes_only_iptc_description(self):
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=True):
            with self._patch_run(self._date_read_result(), _make_completed_process(0)) as mock_run:
                write_exif_description("/p/photo.jpg", description="desc", fmt="iptc")
                cmd = mock_run.call_args_list[1][0][0]
                assert "-IPTC:Caption-Abstract=desc" in cmd
                assert "-ImageDescription=desc" not in cmd
                assert "-XMP:Description=desc" not in cmd

    def test_fmt_exif_writes_only_exif_fields(self):
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=True):
            with self._patch_run(self._date_read_result(), _make_completed_process(0)) as mock_run:
                write_exif_description(
                    "/p/photo.jpg", description="desc", keywords=["kw"], fmt="exif"
                )
                cmd = mock_run.call_args_list[1][0][0]
                assert "-ImageDescription=desc" in cmd
                assert "-UserComment=desc" in cmd
                assert "-XPKeywords=kw" in cmd
                assert "-XMP:Description=desc" not in cmd
                assert "-IPTC:Keywords=kw" not in cmd

    def test_merge_skips_keyword_clear(self):
        """merge=True must preserve existing keywords: idempotent '-=' then '+='
        pairs per keyword, no clearing args, no list-replacing '=' assignments,
        and no XPKeywords (a flat string that cannot be merged)."""
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=True):
            with self._patch_run(self._date_read_result(), _make_completed_process(0)) as mock_run:
                write_exif_description("/p/photo.jpg", keywords=["kw"], merge=True)
                cmd = mock_run.call_args_list[1][0][0]
                # Remove-then-add pairs, in that order.
                assert cmd.index("-IPTC:Keywords-=kw") < cmd.index("-IPTC:Keywords+=kw")
                assert cmd.index("-XMP:Subject-=kw") < cmd.index("-XMP:Subject+=kw")
                # No clear args and no plain '=' (which would replace the list).
                assert "-IPTC:Keywords=" not in cmd
                assert "-IPTC:Keywords=kw" not in cmd
                assert "-XMP:Subject=" not in cmd
                assert "-XMP:Subject=kw" not in cmd
                # XPKeywords skipped entirely in merge mode.
                assert not any(a.startswith("-XPKeywords") for a in cmd)

    def test_no_merge_clears_keywords(self):
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=True):
            with self._patch_run(self._date_read_result(), _make_completed_process(0)) as mock_run:
                write_exif_description("/p/photo.jpg", keywords=["kw"], merge=False)
                cmd = mock_run.call_args_list[1][0][0]
                assert "-IPTC:Keywords=" in cmd  # clear step present


class TestWriteXmpSidecar:
    def _patch_run(self, *side_effects):
        return patch(
            "pyimgtag.exif_writer._run_exiftool",
            side_effect=list(side_effects),
        )

    def test_nothing_to_write_returns_none(self):
        result = write_xmp_sidecar("/path/photo.jpg")
        assert result is None

    def test_exiftool_not_available(self):
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=False):
            result = write_xmp_sidecar("/path/photo.jpg", description="test")
            assert result is not None
            assert "exiftool" in result.lower()

    def test_creates_new_sidecar_from_source(self, tmp_path):
        src = tmp_path / "photo.jpg"
        src.touch()
        # sidecar does not exist yet → expect -o sidecar source args
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=True):
            with self._patch_run(_make_completed_process(0)) as mock_run:
                result = write_xmp_sidecar(str(src), description="desc", keywords=["k1"])
                assert result is None
                cmd = mock_run.call_args_list[0][0][0]
                assert "-XMP:Description=desc" in cmd
                assert "-XMP:Subject=k1" in cmd
                # Should use -o output source pattern (not -overwrite_original)
                assert "-o" in cmd
                assert "-overwrite_original" not in cmd

    def test_updates_existing_sidecar(self, tmp_path):
        src = tmp_path / "photo.jpg"
        src.touch()
        sidecar = tmp_path / "photo.xmp"
        sidecar.touch()
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=True):
            with self._patch_run(_make_completed_process(0)) as mock_run:
                result = write_xmp_sidecar(str(src), description="desc")
                assert result is None
                cmd = mock_run.call_args_list[0][0][0]
                assert "-overwrite_original" in cmd
                assert str(sidecar) in cmd
                assert str(src) not in cmd  # source not passed when updating sidecar

    def test_success_returns_none(self, tmp_path):
        src = tmp_path / "photo.jpg"
        src.touch()
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=True):
            with self._patch_run(_make_completed_process(0)):
                result = write_xmp_sidecar(str(src), description="desc")
                assert result is None

    def test_nonzero_exit_returns_error(self, tmp_path):
        src = tmp_path / "photo.jpg"
        src.touch()
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=True):
            with self._patch_run(_make_completed_process(1, stderr="bad")):
                result = write_xmp_sidecar(str(src), description="desc")
                assert result is not None
                assert "bad" in result

    def test_timeout_returns_error(self, tmp_path):
        src = tmp_path / "photo.jpg"
        src.touch()
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=True):
            with self._patch_run(subprocess.TimeoutExpired(cmd="exiftool", timeout=30)):
                result = write_xmp_sidecar(str(src), description="desc")
                assert result is not None
                assert "timed out" in result.lower()

    def test_clears_subject_before_setting(self, tmp_path):
        src = tmp_path / "photo.jpg"
        src.touch()
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=True):
            with self._patch_run(_make_completed_process(0)) as mock_run:
                write_xmp_sidecar(str(src), keywords=["kw"])
                cmd = mock_run.call_args_list[0][0][0]
                clear_idx = cmd.index("-XMP:Subject=")
                set_idx = cmd.index("-XMP:Subject=kw")
                assert clear_idx < set_idx


class TestReadExistingMetadata:
    def _patch_run(self, stdout="", returncode=0):
        mock = _make_completed_process(returncode, stdout=stdout)
        return patch("pyimgtag.exif_writer._run_exiftool", return_value=mock)

    def test_returns_description_and_keywords(self, tmp_path):
        src = tmp_path / "photo.jpg"
        src.touch()
        payload = json.dumps([{"Description": "A sunset", "Keywords": ["beach", "sun"]}])
        with self._patch_run(payload):
            result = read_existing_metadata(str(src))
        assert result["description"] == "A sunset"
        assert result["keywords"] == ["beach", "sun"]

    def test_single_keyword_as_string(self, tmp_path):
        src = tmp_path / "photo.jpg"
        src.touch()
        payload = json.dumps([{"Keywords": "solo"}])
        with self._patch_run(payload):
            result = read_existing_metadata(str(src))
        assert result["keywords"] == ["solo"]

    def test_empty_response_returns_defaults(self, tmp_path):
        src = tmp_path / "photo.jpg"
        src.touch()
        with self._patch_run("[]"):
            result = read_existing_metadata(str(src))
        assert result["description"] is None
        assert result["keywords"] == []

    def test_nonzero_exit_returns_defaults(self, tmp_path):
        src = tmp_path / "photo.jpg"
        src.touch()
        with self._patch_run("", returncode=1):
            result = read_existing_metadata(str(src))
        assert result["description"] is None
        assert result["keywords"] == []

    def test_prefers_sidecar_when_present(self, tmp_path):
        src = tmp_path / "photo.jpg"
        src.touch()
        sidecar = tmp_path / "photo.xmp"
        sidecar.touch()
        mock = _make_completed_process(0, stdout="[]")
        with patch("pyimgtag.exif_writer._run_exiftool", return_value=mock) as mock_run:
            read_existing_metadata(str(src))
            cmd = mock_run.call_args_list[0][0][0]
            assert str(sidecar) in cmd
            assert str(src) not in cmd


class TestDiffMetadata:
    def test_no_changes_returns_empty(self, tmp_path):
        src = tmp_path / "photo.jpg"
        src.touch()
        existing = {"description": "A sunset", "keywords": ["beach"]}
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=True):
            with patch("pyimgtag.exif_writer.read_existing_metadata", return_value=existing):
                result = diff_metadata(str(src), description="A sunset", keywords=["beach"])
        assert result == []

    def test_description_change_detected(self, tmp_path):
        src = tmp_path / "photo.jpg"
        src.touch()
        existing = {"description": "Old desc", "keywords": []}
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=True):
            with patch("pyimgtag.exif_writer.read_existing_metadata", return_value=existing):
                result = diff_metadata(str(src), description="New desc")
        assert any("description" in line for line in result)
        assert any("New desc" in line for line in result)

    def test_keyword_add_detected(self, tmp_path):
        src = tmp_path / "photo.jpg"
        src.touch()
        existing = {"description": None, "keywords": []}
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=True):
            with patch("pyimgtag.exif_writer.read_existing_metadata", return_value=existing):
                result = diff_metadata(str(src), keywords=["beach", "sunset"])
        assert any("add" in line for line in result)

    def test_keyword_remove_detected(self, tmp_path):
        src = tmp_path / "photo.jpg"
        src.touch()
        existing = {"description": None, "keywords": ["old_tag"]}
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=True):
            with patch("pyimgtag.exif_writer.read_existing_metadata", return_value=existing):
                result = diff_metadata(str(src), keywords=["new_tag"])
        assert any("remove" in line for line in result)

    def test_exiftool_unavailable(self, tmp_path):
        src = tmp_path / "photo.jpg"
        src.touch()
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=False):
            result = diff_metadata(str(src), description="test")
        assert len(result) == 1
        assert "unavailable" in result[0]


class TestReadDateFields:
    """Direct tests for _read_date_fields error/empty paths (lines 72, 75-76)."""

    def test_empty_data_returns_none(self, tmp_path):
        """exiftool returning an empty JSON array yields None (line 72)."""
        src = tmp_path / "photo.jpg"
        src.touch()
        mock = _make_completed_process(0, stdout="[]")
        with patch("pyimgtag.exif_writer._run_exiftool", return_value=mock):
            assert _read_date_fields(str(src)) is None

    def test_returns_only_date_tags_with_values(self, tmp_path):
        """Only known date tags with truthy values are returned."""
        src = tmp_path / "photo.jpg"
        src.touch()
        payload = json.dumps(
            [
                {
                    "SourceFile": "photo.jpg",  # not a date tag → dropped
                    "DateTimeOriginal": "2026:04:01 10:30:00",
                    "CreateDate": "",  # empty → dropped
                }
            ]
        )
        mock = _make_completed_process(0, stdout=payload)
        with patch("pyimgtag.exif_writer._run_exiftool", return_value=mock):
            result = _read_date_fields(str(src))
        assert result == {"DateTimeOriginal": "2026:04:01 10:30:00"}

    def test_timeout_returns_none(self, tmp_path):
        """subprocess.TimeoutExpired is swallowed and yields None (lines 75-76)."""
        src = tmp_path / "photo.jpg"
        src.touch()
        with patch(
            "pyimgtag.exif_writer._run_exiftool",
            side_effect=subprocess.TimeoutExpired(cmd="exiftool", timeout=10),
        ):
            assert _read_date_fields(str(src)) is None

    def test_json_decode_error_returns_none(self, tmp_path):
        """Invalid JSON is swallowed and yields None (lines 75-76)."""
        src = tmp_path / "photo.jpg"
        src.touch()
        mock = _make_completed_process(0, stdout="not json {{{")
        with patch("pyimgtag.exif_writer._run_exiftool", return_value=mock):
            assert _read_date_fields(str(src)) is None

    def test_oserror_returns_none(self, tmp_path):
        """OSError launching exiftool is swallowed and yields None (lines 75-76)."""
        src = tmp_path / "photo.jpg"
        src.touch()
        with patch(
            "pyimgtag.exif_writer._run_exiftool",
            side_effect=OSError("exiftool missing"),
        ):
            assert _read_date_fields(str(src)) is None


class TestWriteXmpSidecarErrors:
    """Extra error paths for write_xmp_sidecar (lines 237-238)."""

    def _patch_run(self, *side_effects):
        return patch(
            "pyimgtag.exif_writer._run_exiftool",
            side_effect=list(side_effects),
        )

    def test_oserror_returns_error(self, tmp_path):
        """OSError launching exiftool yields a 'Failed to launch' message."""
        src = tmp_path / "photo.jpg"
        src.touch()
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=True):
            with self._patch_run(OSError("No such file")):
                result = write_xmp_sidecar(str(src), description="desc")
        assert result is not None
        assert "Failed to launch exiftool" in result
        assert "No such file" in result

    def test_nonzero_exit_no_stderr(self, tmp_path):
        """Non-zero exit without stderr reports the exit code."""
        src = tmp_path / "photo.jpg"
        src.touch()
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=True):
            with self._patch_run(_make_completed_process(2, stderr="")):
                result = write_xmp_sidecar(str(src), description="desc")
        assert result is not None
        assert "2" in result


class TestReadExistingMetadataErrors:
    """Error paths for read_existing_metadata (lines 291-292)."""

    def test_timeout_returns_defaults(self, tmp_path):
        src = tmp_path / "photo.jpg"
        src.touch()
        with patch(
            "pyimgtag.exif_writer._run_exiftool",
            side_effect=subprocess.TimeoutExpired(cmd="exiftool", timeout=10),
        ):
            result = read_existing_metadata(str(src))
        assert result == {"description": None, "keywords": []}

    def test_json_decode_error_returns_defaults(self, tmp_path):
        src = tmp_path / "photo.jpg"
        src.touch()
        mock = _make_completed_process(0, stdout="garbage {{{")
        with patch("pyimgtag.exif_writer._run_exiftool", return_value=mock):
            result = read_existing_metadata(str(src))
        assert result == {"description": None, "keywords": []}

    def test_oserror_returns_defaults(self, tmp_path):
        src = tmp_path / "photo.jpg"
        src.touch()
        with patch(
            "pyimgtag.exif_writer._run_exiftool",
            side_effect=OSError("exiftool missing"),
        ):
            result = read_existing_metadata(str(src))
        assert result == {"description": None, "keywords": []}

    def test_empty_data_list_returns_defaults(self, tmp_path):
        """exiftool returning '[]' (after passing the stdout check) yields defaults."""
        src = tmp_path / "photo.jpg"
        src.touch()
        mock = _make_completed_process(0, stdout="[]")
        with patch("pyimgtag.exif_writer._run_exiftool", return_value=mock):
            result = read_existing_metadata(str(src))
        assert result == {"description": None, "keywords": []}


class TestExtensionConstants:
    def test_raw_sidecar_only_extensions_is_frozen(self):
        assert isinstance(RAW_SIDECAR_ONLY_EXTENSIONS, frozenset)

    def test_cr2_is_sidecar_only(self):
        assert ".cr2" in RAW_SIDECAR_ONLY_EXTENSIONS

    def test_nef_is_sidecar_only(self):
        assert ".nef" in RAW_SIDECAR_ONLY_EXTENSIONS

    def test_arw_is_sidecar_only(self):
        assert ".arw" in RAW_SIDECAR_ONLY_EXTENSIONS

    def test_dng_is_not_sidecar_only(self):
        # DNG supports direct in-file EXIF write via exiftool
        assert ".dng" not in RAW_SIDECAR_ONLY_EXTENSIONS

    def test_dng_supports_direct_write(self):
        assert ".dng" in SUPPORTED_DIRECT_WRITE_EXTENSIONS

    def test_no_overlap_between_sidecar_only_and_direct_write(self):
        overlap = RAW_SIDECAR_ONLY_EXTENSIONS & SUPPORTED_DIRECT_WRITE_EXTENSIONS
        assert overlap == frozenset(), f"Unexpected overlap: {overlap}"


class TestSupportedExtensions:
    def test_common_types_supported(self):
        for ext in (".jpg", ".jpeg", ".heic", ".png", ".tiff", ".tif", ".dng"):
            assert ext in SUPPORTED_DIRECT_WRITE_EXTENSIONS

    def test_raw_types_not_in_supported(self):
        for ext in (".cr2", ".nef", ".raf", ".arw"):
            assert ext not in SUPPORTED_DIRECT_WRITE_EXTENSIONS


class TestIptcCharsetDeclaration:
    """IPTC IIM has no default encoding, so UTF-8 has to be declared.

    A record with no CodedCharacterSet is undeclared, and readers fall back to
    Latin-1 -- so an accented city written without it arrives as mojibake in
    anything that follows the spec. exiftool's own default read assumes UTF-8
    and hides that, which is why the round-trip below forces the Latin-1
    reading rather than trusting a plain read.
    """

    def _patch_run(self, *side_effects):
        return patch(
            "pyimgtag.exif_writer._run_exiftool",
            side_effect=list(side_effects),
        )

    def _date_read_result(self):
        return _make_completed_process(0, stdout=json.dumps([{}]))

    def _cmd_for(self, **kwargs):
        with patch("pyimgtag.exif_writer.is_exiftool_available", return_value=True):
            with self._patch_run(self._date_read_result(), _make_completed_process(0)) as mock_run:
                write_exif_description("/p/photo.jpg", **kwargs)
                return mock_run.call_args_list[1][0][0]

    def test_declared_when_a_description_goes_to_iptc(self):
        assert "-codedcharacterset=utf8" in self._cmd_for(description="desc")

    def test_declared_when_keywords_go_to_iptc(self):
        assert "-codedcharacterset=utf8" in self._cmd_for(keywords=["Óbidos"])

    def test_declared_in_merge_mode(self):
        """Existing undeclared content plus a new declaration is the risky case."""
        cmd = self._cmd_for(keywords=["Óbidos"], merge=True)
        assert "-codedcharacterset=utf8" in cmd
        assert "-IPTC:Keywords+=Óbidos" in cmd

    def test_declared_for_fmt_iptc(self):
        assert "-codedcharacterset=utf8" in self._cmd_for(description="desc", fmt="iptc")

    def test_not_declared_for_fmt_xmp(self):
        """No IPTC text is going out, so a charset record would be noise."""
        assert "-codedcharacterset=utf8" not in self._cmd_for(description="desc", fmt="xmp")

    def test_not_declared_for_fmt_exif(self):
        assert "-codedcharacterset=utf8" not in self._cmd_for(description="desc", fmt="exif")

    def test_not_declared_for_an_unrecognized_fmt(self):
        assert "-codedcharacterset=utf8" not in self._cmd_for(description="desc", fmt="nonsense")

    def test_not_declared_when_only_xmp_only_fields_are_written(self):
        """Hierarchical tags and the rating never reach IPTC."""
        cmd = self._cmd_for(hierarchical=["Places|Portugal"], rating=4)
        assert "-codedcharacterset=utf8" not in cmd

    @staticmethod
    def _write_accented(tmp_path, name: str) -> "object":
        from PIL import Image

        photo = tmp_path / name
        Image.new("RGB", (16, 16), (9, 9, 9)).save(photo)
        assert (
            write_exif_description(
                str(photo), description="Sunset at Óbidos", keywords=["Óbidos", "José"]
            )
            is None
        )
        return photo

    @pytest.mark.skipif(not shutil.which("exiftool"), reason="requires exiftool on PATH")
    def test_the_declaration_reaches_the_file(self, tmp_path):
        """The regression guard: without this arg the record has no charset.

        exiftool cannot stand in for a strict IPTC reader here -- it
        auto-detects valid UTF-8 in the record and decodes it correctly whether
        or not anything was declared, which is exactly why this went unnoticed.
        What can be asserted is that the declaration is present, which is what
        a reader that does *not* guess needs in order to get it right. That
        assertion is ASCII, so it holds on every platform including the one
        where #366 destroys the values themselves.
        """
        photo = self._write_accented(tmp_path, "a.jpg")
        data = _exiftool_json(photo, "-IPTC:CodedCharacterSet")
        assert data["CodedCharacterSet"] == "UTF8"

    @pytest.mark.skipif(not shutil.which("exiftool"), reason="requires exiftool on PATH")
    def test_the_declared_values_survive(self, tmp_path):
        """Declaring the charset is worth nothing if the bytes arrive broken.

        They used to on Windows, destroyed in the argv code-page conversion
        before exiftool saw them, so the record correctly declared UTF-8 over
        content that was already '?bidos'. #366 moved the handover into a
        UTF-8 argument file and this now runs everywhere.
        """
        photo = self._write_accented(tmp_path, "a.jpg")
        data = _exiftool_json(photo, "-IPTC:Keywords", "-IPTC:Caption-Abstract")
        assert data["Keywords"] == ["Óbidos", "José"]
        assert data["Caption-Abstract"] == "Sunset at Óbidos"

    @pytest.mark.skipif(not shutil.which("exiftool"), reason="requires exiftool on PATH")
    def test_no_declaration_for_an_xmp_only_write(self, tmp_path):
        from PIL import Image

        photo = tmp_path / "b.jpg"
        Image.new("RGB", (16, 16), (9, 9, 9)).save(photo)

        assert write_exif_description(str(photo), description="desc", fmt="xmp") is None

        data = _exiftool_json(photo, "-IPTC:CodedCharacterSet")
        assert "CodedCharacterSet" not in data


class TestNonAsciiRoundTrip:
    """#366: the bug was in the file, not in the argument vector."""

    @pytest.mark.skipif(not shutil.which("exiftool"), reason="requires exiftool on PATH")
    def test_accented_keywords_and_paths_survive(self, tmp_path):
        from PIL import Image

        # The directory name is non-ASCII too: paths went through the same
        # conversion as values, so a photo under ~/Fotos/Óbidos was the same bug.
        folder = tmp_path / "Óbidos"
        folder.mkdir()
        photo = folder / "praça.jpg"
        Image.new("RGB", (16, 16), (9, 9, 9)).save(photo)

        assert (
            write_exif_description(
                str(photo),
                description="Sunset at Óbidos",
                keywords=["Óbidos", "José", "Kraków"],
                hierarchical=["Places|Portugal|Leiria|Óbidos"],
            )
            is None
        )

        data = _exiftool_json(
            photo, "-XMP:Subject", "-XMP:Description", "-XMP-lr:HierarchicalSubject"
        )
        assert data["Subject"] == ["Óbidos", "José", "Kraków"]
        assert data["Description"] == "Sunset at Óbidos"
        assert data["HierarchicalSubject"] == "Places|Portugal|Leiria|Óbidos"

        # Deliberately not asserting over the whole JSON: exiftool echoes
        # SourceFile back through its own filename charset, which is the system
        # code page on Windows, so the path in the *output* can read "?bidos"
        # for a file it nonetheless opened and wrote correctly -- as the three
        # assertions above, all read back from that very path, show.
        values = [data["Description"], data["HierarchicalSubject"], *data["Subject"]]
        assert not any("?" in v for v in values), values

    @pytest.mark.skipif(not shutil.which("exiftool"), reason="requires exiftool on PATH")
    def test_a_multiline_description_is_not_truncated(self, tmp_path):
        """The argfile hazard: exiftool reports success while losing line two."""
        from PIL import Image

        photo = tmp_path / "a.jpg"
        Image.new("RGB", (16, 16), (9, 9, 9)).save(photo)

        assert (
            write_exif_description(str(photo), description="line one\nline two with Óbidos") is None
        )

        data = _exiftool_json(photo, "-XMP:Description")
        assert data["Description"] == "line one\nline two with Óbidos"
