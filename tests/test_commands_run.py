"""Tests for the run subcommand — edge cases not covered elsewhere."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pyimgtag.scanner import scan_directory


class TestNewestFirstSort:
    """Bug: newest_first sort crashes if a file is deleted between scan and sort."""

    def test_sort_survives_deleted_file(self, tmp_path: Path) -> None:
        """Files deleted after scanning must be handled gracefully, not crash."""
        p1 = tmp_path / "a.jpg"
        p2 = tmp_path / "b.jpg"
        p1.write_bytes(b"x")
        p2.write_bytes(b"x")

        files = [p1, p2]
        p2.unlink()

        # Reproduce the sort from cmd_run with the fix applied
        def _mtime(f: Path) -> float:
            try:
                return f.stat().st_mtime
            except OSError:
                return 0.0

        files.sort(key=_mtime, reverse=True)
        assert p1 in files

    def test_cmd_run_newest_first_with_deleted_file(self, tmp_path: Path) -> None:
        """cmd_run --newest-first must not raise when a file disappears mid-run."""
        from pyimgtag.commands.run import cmd_run

        img = tmp_path / "photo.jpg"
        img.write_bytes(b"x")
        deleted = tmp_path / "deleted.jpg"
        deleted.write_bytes(b"x")

        args = MagicMock()
        args.input_dir = str(tmp_path)
        args.photos_library = None
        args.extensions = "jpg"
        args.newest_first = True
        args.no_cache = True
        args.dedup = False
        args.limit = None
        args.date = None
        args.date_from = None
        args.date_to = None
        args.skip_no_gps = False
        args.write_back = False
        args.write_exif = False
        args.sidecar_only = False
        args.dry_run = True
        args.verbose = False
        args.jsonl_stdout = False
        args.output_json = None
        args.output_csv = None
        args.ollama_url = "http://localhost:11434"
        args.model = "test"
        args.max_dim = 512
        args.timeout = 5
        args.cache_dir = None

        def mock_tag_image(path, context=None):
            from pyimgtag.models import TagResult

            return TagResult(tags=["test"], summary="test")

        with (
            patch("pyimgtag.commands.run.check_ollama", return_value=(True, "")),
            patch("pyimgtag.commands.run.OllamaClient") as mock_client_cls,
        ):
            mock_client = MagicMock()
            mock_client.tag_image.side_effect = mock_tag_image
            mock_client_cls.return_value = mock_client

            # Delete the file AFTER scan, BEFORE sort runs inside cmd_run
            original_scan = __import__(
                "pyimgtag.scanner", fromlist=["scan_directory"]
            ).scan_directory

            def patched_scan(path, extensions, recursive=True):
                result = original_scan(path, extensions, recursive=recursive)
                deleted.unlink()  # simulate race condition
                return result

            with patch("pyimgtag.commands.run.scan_directory", side_effect=patched_scan):
                # Must not raise OSError
                rc = cmd_run(args, MagicMock())
        assert rc == 0


class TestExtensionsWithDots:
    """Bug: --extensions with leading dots (e.g. '.jpg') finds no files."""

    def test_scan_directory_ignores_dotted_extensions(self, tmp_path: Path) -> None:
        """scan_directory receives exts WITHOUT dots by convention."""
        (tmp_path / "photo.jpg").write_bytes(b"x")
        # Dotted set — reveals the bug
        files = scan_directory(tmp_path, extensions={".jpg"})
        assert len(files) == 0  # documents current behavior (bug is in cmd_run parsing)

    def test_cmd_run_strips_leading_dots_from_extensions(self, tmp_path: Path) -> None:
        """cmd_run must strip leading dots so '--extensions .jpg' works like 'jpg'."""
        from pyimgtag.commands.run import cmd_run

        img = tmp_path / "photo.jpg"
        img.write_bytes(b"x")

        args = MagicMock()
        args.input_dir = str(tmp_path)
        args.photos_library = None
        args.extensions = ".jpg"  # user passes dotted extension
        args.newest_first = False
        args.no_cache = True
        args.dedup = False
        args.limit = None
        args.date = None
        args.date_from = None
        args.date_to = None
        args.skip_no_gps = False
        args.write_back = False
        args.write_exif = False
        args.sidecar_only = False
        args.dry_run = True
        args.verbose = False
        args.jsonl_stdout = False
        args.output_json = None
        args.output_csv = None
        args.ollama_url = "http://localhost:11434"
        args.model = "test"
        args.max_dim = 512
        args.timeout = 5
        args.cache_dir = None

        processed_files: list[str] = []

        def mock_tag_image(path, context=None):
            from pyimgtag.models import TagResult

            processed_files.append(path)
            return TagResult(tags=["test"], summary="test image")

        with (
            patch("pyimgtag.commands.run.check_ollama", return_value=(True, "")),
            patch("pyimgtag.commands.run.OllamaClient") as mock_client_cls,
        ):
            mock_client = MagicMock()
            mock_client.tag_image.side_effect = mock_tag_image
            mock_client_cls.return_value = mock_client
            rc = cmd_run(args, MagicMock())

        assert rc == 0
        assert len(processed_files) == 1, (
            f"Expected photo.jpg to be processed when --extensions .jpg, got: {processed_files}"
        )
