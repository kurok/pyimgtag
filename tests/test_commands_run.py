"""Tests for the run subcommand — edge cases not covered elsewhere."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from pyimgtag.models import ExifData
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


class TestPhotosLibraryPermissionDialog:
    """PermissionError from Photos Library scan triggers osascript dialog on macOS."""

    def _make_args(self, tmp_path: Path) -> MagicMock:
        args = MagicMock()
        args.input_dir = None
        args.photos_library = str(tmp_path)
        args.extensions = "jpg"
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
        args.no_recursive = False
        return args

    def test_dialog_invoked_on_permission_error(self, tmp_path: Path) -> None:
        """cmd_run must call _request_photos_access_dialog when scan raises PermissionError."""
        from pyimgtag.commands.run import cmd_run

        args = self._make_args(tmp_path)

        with (
            patch("pyimgtag.commands.run.check_ollama", return_value=(True, "")),
            patch(
                "pyimgtag.commands.run.scan_photos_library",
                side_effect=PermissionError("denied"),
            ),
            patch("pyimgtag.commands.run._request_photos_access_dialog") as mock_dialog,
        ):
            rc = cmd_run(args, MagicMock())

        assert rc == 1
        mock_dialog.assert_called_once()

    def test_dialog_not_invoked_for_file_not_found(self, tmp_path: Path) -> None:
        """FileNotFoundError must NOT trigger the dialog."""
        from pyimgtag.commands.run import cmd_run

        args = self._make_args(tmp_path)

        with (
            patch("pyimgtag.commands.run.check_ollama", return_value=(True, "")),
            patch(
                "pyimgtag.commands.run.scan_photos_library",
                side_effect=FileNotFoundError("not found"),
            ),
            patch("pyimgtag.commands.run._request_photos_access_dialog") as mock_dialog,
        ):
            rc = cmd_run(args, MagicMock())

        assert rc == 1
        mock_dialog.assert_not_called()

    def test_request_photos_access_dialog_skips_non_macos(self) -> None:
        """Dialog helper must be a no-op on non-macOS platforms."""
        from pyimgtag.commands.run import _request_photos_access_dialog

        with (
            patch("pyimgtag.commands.run.get_platform_name", return_value="Linux"),
            patch("pyimgtag.commands.run.subprocess.run") as mock_run,
        ):
            _request_photos_access_dialog()

        mock_run.assert_not_called()

    def test_request_photos_access_dialog_skips_when_osascript_missing(self) -> None:
        """Dialog helper must be a no-op when osascript is not on PATH."""
        from pyimgtag.commands.run import _request_photos_access_dialog

        with (
            patch("pyimgtag.commands.run.get_platform_name", return_value="Darwin"),
            patch("pyimgtag.commands.run.shutil.which", return_value=None),
            patch("pyimgtag.commands.run.subprocess.run") as mock_run,
        ):
            _request_photos_access_dialog()

        mock_run.assert_not_called()


class TestWriteBackDryRun:
    """--write-back must be suppressed when --dry-run is set."""

    def _make_args(self, tmp_path: Path, *, dry_run: bool) -> MagicMock:
        args = MagicMock()
        args.input_dir = None
        args.photos_library = str(tmp_path)
        args.extensions = "jpg"
        args.newest_first = False
        args.no_cache = True
        args.dedup = False
        args.limit = None
        args.date = None
        args.date_from = None
        args.date_to = None
        args.skip_no_gps = False
        args.write_back = True
        args.write_exif = False
        args.sidecar_only = False
        args.dry_run = dry_run
        args.verbose = False
        args.jsonl_stdout = False
        args.output_json = None
        args.output_csv = None
        args.ollama_url = "http://localhost:11434"
        args.model = "test"
        args.max_dim = 512
        args.timeout = 5
        args.cache_dir = None
        args.no_recursive = False
        return args

    def test_write_back_skipped_in_dry_run(self, tmp_path: Path) -> None:
        """write_to_photos must NOT be called when --dry-run is active."""
        from pyimgtag.commands.run import cmd_run
        from pyimgtag.models import TagResult

        img = tmp_path / "AABBCCDD.jpg"
        img.write_bytes(b"x")
        args = self._make_args(tmp_path, dry_run=True)

        with (
            patch("pyimgtag.commands.run.check_ollama", return_value=(True, "")),
            patch("pyimgtag.commands.run.OllamaClient") as mock_client_cls,
            patch("pyimgtag.commands.run.scan_photos_library", return_value=[img]),
            patch("pyimgtag.applescript_writer.write_to_photos") as mock_write,
        ):
            mock_client = MagicMock()
            mock_client.tag_image.return_value = TagResult(tags=["tag"], summary="desc")
            mock_client_cls.return_value = mock_client
            cmd_run(args, MagicMock())

        mock_write.assert_not_called()

    def test_write_back_mode_forwarded(self, tmp_path: Path) -> None:
        """write_to_photos must receive the write_back_mode from args."""
        from pyimgtag.commands.run import cmd_run
        from pyimgtag.models import TagResult

        img = tmp_path / "AABBCCDD.jpg"
        img.write_bytes(b"x")
        args = self._make_args(tmp_path, dry_run=False)
        args.write_back_mode = "append"

        with (
            patch("pyimgtag.commands.run.check_ollama", return_value=(True, "")),
            patch("pyimgtag.commands.run.OllamaClient") as mock_client_cls,
            patch("pyimgtag.commands.run.scan_photos_library", return_value=[img]),
            patch("pyimgtag.applescript_writer.write_to_photos", return_value=None) as mock_write,
        ):
            mock_client = MagicMock()
            mock_client.tag_image.return_value = TagResult(tags=["tag"], summary="desc")
            mock_client_cls.return_value = mock_client
            cmd_run(args, MagicMock())

        _, kwargs = mock_write.call_args
        assert kwargs.get("mode") == "append"

    def test_write_back_runs_without_dry_run(self, tmp_path: Path) -> None:
        """write_to_photos must be called when --write-back is set and --dry-run is not."""
        from pyimgtag.commands.run import cmd_run
        from pyimgtag.models import TagResult

        img = tmp_path / "AABBCCDD.jpg"
        img.write_bytes(b"x")
        args = self._make_args(tmp_path, dry_run=False)

        with (
            patch("pyimgtag.commands.run.check_ollama", return_value=(True, "")),
            patch("pyimgtag.commands.run.OllamaClient") as mock_client_cls,
            patch("pyimgtag.commands.run.scan_photos_library", return_value=[img]),
            patch("pyimgtag.applescript_writer.write_to_photos", return_value=None) as mock_write,
        ):
            mock_client = MagicMock()
            mock_client.tag_image.return_value = TagResult(tags=["tag"], summary="desc")
            mock_client_cls.return_value = mock_client
            cmd_run(args, MagicMock())

        mock_write.assert_called_once()


class TestSkipIfTagged:
    """--skip-if-tagged skips Ollama processing for photos already tagged in Photos."""

    def _make_args(self, tmp_path: Path) -> MagicMock:
        args = MagicMock()
        args.input_dir = None
        args.photos_library = str(tmp_path)
        args.extensions = "jpg"
        args.newest_first = False
        args.no_cache = True
        args.dedup = False
        args.limit = None
        args.date = None
        args.date_from = None
        args.date_to = None
        args.skip_no_gps = False
        args.skip_if_tagged = True
        args.write_back = False
        args.write_exif = False
        args.sidecar_only = False
        args.dry_run = False
        args.verbose = False
        args.jsonl_stdout = False
        args.output_json = None
        args.output_csv = None
        args.ollama_url = "http://localhost:11434"
        args.model = "test"
        args.max_dim = 512
        args.timeout = 5
        args.cache_dir = None
        args.no_recursive = False
        return args

    def test_skips_ollama_when_photo_already_has_keywords(self, tmp_path: Path) -> None:
        """Photo with existing keywords in Photos must not reach Ollama."""
        from pyimgtag.commands.run import cmd_run

        img = tmp_path / "photo.jpg"
        img.write_bytes(b"x")
        args = self._make_args(tmp_path)

        with (
            patch("pyimgtag.commands.run.check_ollama", return_value=(True, "")),
            patch("pyimgtag.commands.run.OllamaClient") as mock_client_cls,
            patch("pyimgtag.commands.run.scan_photos_library", return_value=[img]),
            patch(
                "pyimgtag.commands.run.read_keywords_from_photos",
                return_value=["sunset", "beach"],
            ),
        ):
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client
            cmd_run(args, MagicMock())

        mock_client.tag_image.assert_not_called()

    def test_processes_photo_with_no_keywords(self, tmp_path: Path) -> None:
        """Photo with empty keyword list must still be processed by Ollama."""
        from pyimgtag.commands.run import cmd_run
        from pyimgtag.models import TagResult

        img = tmp_path / "photo.jpg"
        img.write_bytes(b"x")
        args = self._make_args(tmp_path)

        with (
            patch("pyimgtag.commands.run.check_ollama", return_value=(True, "")),
            patch("pyimgtag.commands.run.OllamaClient") as mock_client_cls,
            patch("pyimgtag.commands.run.scan_photos_library", return_value=[img]),
            patch("pyimgtag.commands.run.read_keywords_from_photos", return_value=[]),
        ):
            mock_client = MagicMock()
            mock_client.tag_image.return_value = TagResult(tags=["nature"], summary="")
            mock_client_cls.return_value = mock_client
            cmd_run(args, MagicMock())

        mock_client.tag_image.assert_called_once()

    def test_processes_photo_when_read_returns_none(self, tmp_path: Path) -> None:
        """If keyword read fails (None), photo must be processed rather than silently skipped."""
        from pyimgtag.commands.run import cmd_run
        from pyimgtag.models import TagResult

        img = tmp_path / "photo.jpg"
        img.write_bytes(b"x")
        args = self._make_args(tmp_path)

        with (
            patch("pyimgtag.commands.run.check_ollama", return_value=(True, "")),
            patch("pyimgtag.commands.run.OllamaClient") as mock_client_cls,
            patch("pyimgtag.commands.run.scan_photos_library", return_value=[img]),
            patch("pyimgtag.commands.run.read_keywords_from_photos", return_value=None),
        ):
            mock_client = MagicMock()
            mock_client.tag_image.return_value = TagResult(tags=["nature"], summary="")
            mock_client_cls.return_value = mock_client
            cmd_run(args, MagicMock())

        mock_client.tag_image.assert_called_once()

    def test_skip_if_tagged_false_does_not_read_keywords(self, tmp_path: Path) -> None:
        """When --skip-if-tagged is off, read_keywords_from_photos must not be called."""
        from pyimgtag.commands.run import cmd_run
        from pyimgtag.models import TagResult

        img = tmp_path / "photo.jpg"
        img.write_bytes(b"x")
        args = self._make_args(tmp_path)
        args.skip_if_tagged = False

        with (
            patch("pyimgtag.commands.run.check_ollama", return_value=(True, "")),
            patch("pyimgtag.commands.run.OllamaClient") as mock_client_cls,
            patch("pyimgtag.commands.run.scan_photos_library", return_value=[img]),
            patch("pyimgtag.commands.run.read_keywords_from_photos") as mock_read,
        ):
            mock_client = MagicMock()
            mock_client.tag_image.return_value = TagResult(tags=["nature"], summary="")
            mock_client_cls.return_value = mock_client
            cmd_run(args, MagicMock())

        mock_read.assert_not_called()


class TestRunSessionWiring:
    def test_no_web_does_not_register_session(self, tmp_path):
        """--no-web leaves the RunRegistry empty."""
        from pyimgtag import run_registry
        from pyimgtag.commands.run import cmd_run
        from pyimgtag.main import build_parser

        run_registry.set_current(None)

        img = tmp_path / "a.jpg"
        img.write_bytes(b"x")

        parser = build_parser()
        args = parser.parse_args(
            [
                "run",
                "--input-dir",
                str(tmp_path),
                "--extensions",
                "jpg",
                "--no-web",
                "--no-cache",
                "--dry-run",
            ]
        )

        # Avoid real Ollama / geocoder / exif work by monkeypatching at runtime.
        with (
            patch("pyimgtag.commands.run.OllamaClient") as ollama_cls,
            patch("pyimgtag.commands.run.ReverseGeocoder") as geo_cls,
            patch("pyimgtag.commands.run.check_ollama", return_value=(True, "ok")),
            patch("pyimgtag.commands.run.read_exif", return_value=ExifData()),
        ):
            ollama = MagicMock()
            ollama.tag_image.return_value = MagicMock(
                error=None,
                tags=["x"],
                summary=None,
                scene_category=None,
                emotional_tone=None,
                cleanup_class=None,
                has_text=False,
                text_summary=None,
                event_hint=None,
                significance=None,
            )
            ollama_cls.return_value = ollama
            geo_cls.return_value = MagicMock()
            rc = cmd_run(args, parser)

        assert rc == 0
        assert run_registry.get_current() is None

    def test_web_enabled_registers_and_clears_session(self, tmp_path):
        """With the default (web on), the registry is populated during the run
        and cleared after it returns."""
        import pytest

        pytest.importorskip("fastapi")
        pytest.importorskip("uvicorn")

        from pyimgtag import run_registry
        from pyimgtag.commands.run import cmd_run
        from pyimgtag.main import build_parser

        run_registry.set_current(None)

        img = tmp_path / "a.jpg"
        img.write_bytes(b"x")

        parser = build_parser()
        # Use port 0 trick is tricky with uvicorn.Config; pick a fixed, likely-free port.
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            free_port = s.getsockname()[1]

        args = parser.parse_args(
            [
                "run",
                "--input-dir",
                str(tmp_path),
                "--extensions",
                "jpg",
                "--web",
                "--web-port",
                str(free_port),
                "--no-browser",
                "--no-cache",
                "--dry-run",
            ]
        )

        seen: list[bool] = []

        def fake_tag_image(*a, **kw):
            seen.append(run_registry.get_current() is not None)
            return MagicMock(
                error=None,
                tags=["x"],
                summary=None,
                scene_category=None,
                emotional_tone=None,
                cleanup_class=None,
                has_text=False,
                text_summary=None,
                event_hint=None,
                significance=None,
            )

        with (
            patch("pyimgtag.commands.run.OllamaClient") as ollama_cls,
            patch("pyimgtag.commands.run.ReverseGeocoder") as geo_cls,
            patch("pyimgtag.commands.run.check_ollama", return_value=(True, "ok")),
            patch("pyimgtag.commands.run.read_exif", return_value=ExifData()),
        ):
            ollama = MagicMock()
            ollama.tag_image.side_effect = fake_tag_image
            ollama_cls.return_value = ollama
            geo_cls.return_value = MagicMock()
            rc = cmd_run(args, parser)

        assert rc == 0
        assert seen == [True]  # session registered during processing
        assert run_registry.get_current() is None  # cleared after teardown


class TestDryRunNoDbWrites:
    """Regression: --dry-run must not create or write to the progress DB."""

    def test_dry_run_does_not_create_db(self, tmp_path: Path) -> None:
        """cmd_run with --dry-run must leave the DB file absent after the run."""
        from pyimgtag.commands.run import cmd_run
        from pyimgtag.models import TagResult

        img = tmp_path / "photo.jpg"
        img.write_bytes(b"x")
        db_path = tmp_path / "progress.db"

        args = MagicMock()
        args.input_dir = str(tmp_path)
        args.photos_library = None
        args.extensions = "jpg"
        args.newest_first = False
        args.no_cache = False  # DB would normally be opened
        args.dry_run = True
        args.dedup = False
        args.limit = None
        args.date = None
        args.date_from = None
        args.date_to = None
        args.skip_no_gps = False
        args.write_back = False
        args.write_exif = False
        args.sidecar_only = False
        args.verbose = False
        args.jsonl_stdout = False
        args.output_json = None
        args.output_csv = None
        args.ollama_url = "http://localhost:11434"
        args.model = "test"
        args.max_dim = 512
        args.timeout = 5
        args.cache_dir = None
        args.db = str(db_path)

        assert not db_path.exists(), "precondition: DB must not exist before the run"

        with (
            patch("pyimgtag.commands.run.check_ollama", return_value=(True, "")),
            patch("pyimgtag.commands.run.OllamaClient") as mock_client_cls,
        ):
            mock_client = MagicMock()
            mock_client.tag_image.return_value = TagResult(tags=["test"], summary="test")
            mock_client_cls.return_value = mock_client
            rc = cmd_run(args, MagicMock())

        assert rc == 0
        assert not db_path.exists(), "DB must not be created by --dry-run"


class TestSequentialPauseGate:
    def test_pause_blocks_before_next_file_and_resume_continues(self, tmp_path):
        """Pause must stop processing before the next file; resume must continue."""
        import threading
        import time

        from pyimgtag import run_registry
        from pyimgtag.commands.run import cmd_run
        from pyimgtag.main import build_parser

        run_registry.set_current(None)

        for i in range(3):
            (tmp_path / f"{i}.jpg").write_bytes(b"x")

        parser = build_parser()
        args = parser.parse_args(
            [
                "run",
                "--input-dir",
                str(tmp_path),
                "--extensions",
                "jpg",
                "--no-web",
                "--no-cache",
                "--dry-run",
            ]
        )

        # Build a session and attach it manually via registry so --no-web
        # still exercises the pause check inline.
        from pyimgtag.run_session import RunSession

        session = RunSession(command="run")
        run_registry.set_current(session)

        processed_paths: list[str] = []
        pause_after_first = threading.Event()
        pause_requested = threading.Event()

        def fake_tag(path, *a, **kw):
            processed_paths.append(path)
            if len(processed_paths) == 1:
                pause_after_first.set()
                # Block the first call until the test has issued request_pause(),
                # so the loop can't race past the next wait_if_paused() check.
                pause_requested.wait(timeout=5.0)
            return MagicMock(
                error=None,
                tags=[],
                summary=None,
                scene_category=None,
                emotional_tone=None,
                cleanup_class=None,
                has_text=False,
                text_summary=None,
                event_hint=None,
                significance=None,
            )

        result_holder: dict = {}

        def run_cmd():
            result_holder["rc"] = cmd_run(args, parser)

        with (
            patch("pyimgtag.commands.run.OllamaClient") as ollama_cls,
            patch("pyimgtag.commands.run.ReverseGeocoder") as geo_cls,
            patch("pyimgtag.commands.run.check_ollama", return_value=(True, "ok")),
            patch("pyimgtag.commands.run.read_exif", return_value=ExifData()),
            patch("pyimgtag.webapp.bootstrap.start_dashboard_for", return_value=(session, None)),
        ):
            ollama = MagicMock()
            ollama.tag_image.side_effect = fake_tag
            ollama_cls.return_value = ollama
            geo_cls.return_value = MagicMock()

            worker = threading.Thread(target=run_cmd, daemon=True)
            worker.start()

            assert pause_after_first.wait(timeout=2.0)
            session.request_pause()
            pause_requested.set()  # release the first fake_tag call

            # Give the loop up to 1s to reach PAUSED.
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                if session.snapshot()["state"] == "paused":
                    break
                time.sleep(0.02)
            assert session.snapshot()["state"] == "paused"
            assert len(processed_paths) == 1

            session.resume()
            worker.join(timeout=3.0)

        assert result_holder["rc"] == 0
        assert len(processed_paths) == 3
        run_registry.set_current(None)


class TestThreadedPauseGate:
    def test_threaded_branch_hits_pause_gate_and_records_items(self, tmp_path):
        """--resume-from-db --resume-threaded exercises the threaded loop; the pause
        gate must block between fresh files just like the sequential branch."""
        import threading
        import time

        from pyimgtag import run_registry
        from pyimgtag.commands.run import cmd_run
        from pyimgtag.main import build_parser
        from pyimgtag.models import ImageResult
        from pyimgtag.progress_db import ProgressDB
        from pyimgtag.run_session import RunSession

        run_registry.set_current(None)

        db_path = tmp_path / "progress.db"
        cached_img = tmp_path / "cached.jpg"
        cached_img.write_bytes(b"x")
        fresh0 = tmp_path / "fresh0.jpg"
        fresh0.write_bytes(b"x")
        fresh1 = tmp_path / "fresh1.jpg"
        fresh1.write_bytes(b"x")

        # Seed the DB with a cached model result so the threaded cache-worker
        # has something to hydrate.
        db = ProgressDB(db_path=db_path)
        db.mark_done(
            cached_img,
            ImageResult(
                file_path=str(cached_img),
                file_name=cached_img.name,
                tags=["seed"],
                scene_summary="seeded",
            ),
        )
        db.close()

        parser = build_parser()
        args = parser.parse_args(
            [
                "run",
                "--input-dir",
                str(tmp_path),
                "--extensions",
                "jpg",
                "--no-web",
                "--resume-from-db",
                "--resume-threaded",
                "--db",
                str(db_path),
            ]
        )

        session = RunSession(command="run")
        run_registry.set_current(session)

        processed_paths: list[str] = []
        pause_after_first_fresh = threading.Event()
        pause_requested = threading.Event()

        def fake_tag(path, *a, **kw):
            processed_paths.append(path)
            if len(processed_paths) == 1:
                pause_after_first_fresh.set()
                pause_requested.wait(timeout=5.0)
            return MagicMock(
                error=None,
                tags=[],
                summary=None,
                scene_category=None,
                emotional_tone=None,
                cleanup_class=None,
                has_text=False,
                text_summary=None,
                event_hint=None,
                significance=None,
            )

        result_holder: dict = {}

        def run_cmd():
            result_holder["rc"] = cmd_run(args, parser)

        with (
            patch("pyimgtag.commands.run.OllamaClient") as ollama_cls,
            patch("pyimgtag.commands.run.ReverseGeocoder") as geo_cls,
            patch("pyimgtag.commands.run.check_ollama", return_value=(True, "ok")),
            patch("pyimgtag.commands.run.read_exif", return_value=ExifData()),
            patch("pyimgtag.webapp.bootstrap.start_dashboard_for", return_value=(session, None)),
        ):
            ollama = MagicMock()
            ollama.tag_image.side_effect = fake_tag
            ollama_cls.return_value = ollama
            geo_cls.return_value = MagicMock()

            worker = threading.Thread(target=run_cmd, daemon=True)
            worker.start()

            assert pause_after_first_fresh.wait(timeout=3.0), (
                f"first fresh file never reached fake_tag; processed={processed_paths}"
            )
            session.request_pause()
            pause_requested.set()

            deadline = time.monotonic() + 1.5
            while time.monotonic() < deadline:
                if session.snapshot()["state"] == "paused":
                    break
                time.sleep(0.02)
            assert session.snapshot()["state"] == "paused"
            # Only one fresh file was processed before the gate blocked.
            assert len(processed_paths) == 1

            session.resume()
            worker.join(timeout=5.0)

        assert result_holder["rc"] == 0
        # Both fresh files eventually processed.
        assert len(processed_paths) == 2
        # Recent-events buffer saw the fresh file hook; counters populated.
        snap = session.snapshot()
        assert snap["recent"], "expected at least one recorded fresh-file event"
        assert "processed" in snap["counters"]
        run_registry.set_current(None)


# ---------------------------------------------------------------------------
# _write_metadata
# ---------------------------------------------------------------------------


class TestWriteMetadata:
    """Unit tests for the _write_metadata helper (sidecar / direct / auto-fallback)."""

    def _make_result(self, tmp_path, suffix=".jpg"):
        from pyimgtag.models import ImageResult

        p = tmp_path / f"photo{suffix}"
        p.write_bytes(b"fake")
        result = MagicMock(spec=ImageResult)
        result.file_path = str(p)
        result.tags = ["nature", "sky"]
        return result

    def _make_args(self, *, sidecar_only=False, metadata_format="auto"):
        args = MagicMock()
        args.sidecar_only = sidecar_only
        args.metadata_format = metadata_format
        return args

    def test_sidecar_only_calls_write_xmp_sidecar(self, tmp_path):
        from pyimgtag.commands.run import _write_metadata

        result = self._make_result(tmp_path)
        args = self._make_args(sidecar_only=True)

        with patch("pyimgtag.exif_writer.write_xmp_sidecar", return_value=None) as mock_sidecar:
            _write_metadata(result, "A description", args)

        mock_sidecar.assert_called_once()

    def test_unsupported_extension_falls_back_to_sidecar(self, tmp_path):
        from pyimgtag.commands.run import _write_metadata

        result = self._make_result(tmp_path, suffix=".cr2")
        args = self._make_args(sidecar_only=False)

        with (
            patch("pyimgtag.exif_writer.write_xmp_sidecar", return_value=None) as mock_sidecar,
            patch("pyimgtag.exif_writer.write_exif_description") as mock_exif,
        ):
            _write_metadata(result, "desc", args)

        mock_sidecar.assert_called_once()
        mock_exif.assert_not_called()

    def test_supported_extension_calls_write_exif_description(self, tmp_path):
        from pyimgtag.commands.run import _write_metadata

        result = self._make_result(tmp_path, suffix=".jpg")
        args = self._make_args(sidecar_only=False, metadata_format="xmp")

        with patch("pyimgtag.exif_writer.write_exif_description", return_value=None) as mock_exif:
            _write_metadata(result, "desc", args)

        mock_exif.assert_called_once()

    def test_write_exif_failure_prints_to_stderr(self, tmp_path, capsys):
        from pyimgtag.commands.run import _write_metadata

        result = self._make_result(tmp_path, suffix=".jpg")
        args = self._make_args(sidecar_only=False)

        with patch("pyimgtag.exif_writer.write_exif_description", return_value="exiftool died"):
            _write_metadata(result, "desc", args)

        captured = capsys.readouterr()
        assert "EXIF write failed" in captured.err


# ---------------------------------------------------------------------------
# _hydrate_from_db (resume-from-db path)
# ---------------------------------------------------------------------------


class TestHydrateFromDb:
    """Tests for the _hydrate_from_db helper that loads cached ImageResults."""

    def _make_args(self, *, date=None, date_from=None, date_to=None, skip_no_gps=False):
        args = MagicMock()
        args.date = date
        args.date_from = date_from
        args.date_to = date_to
        args.skip_no_gps = skip_no_gps
        return args

    def test_returns_none_when_not_in_db(self, tmp_path):
        from pyimgtag.commands.run import _hydrate_from_db
        from pyimgtag.geocoder import ReverseGeocoder
        from pyimgtag.progress_db import ProgressDB

        img = tmp_path / "photo.jpg"
        img.write_bytes(b"x")
        geocoder = MagicMock(spec=ReverseGeocoder)

        with ProgressDB(db_path=tmp_path / "test.db") as db:
            stats = {
                "skipped_date": 0,
                "skipped_no_gps": 0,
                "geocode_failures": 0,
                "resumed_from_db": 0,
            }
            result = _hydrate_from_db(img, "directory", self._make_args(), geocoder, stats, db)

        assert result is None

    def test_loads_result_from_db(self, tmp_path):
        from pyimgtag.commands.run import _hydrate_from_db
        from pyimgtag.geocoder import ReverseGeocoder
        from pyimgtag.models import ImageResult
        from pyimgtag.progress_db import ProgressDB

        img = tmp_path / "photo.jpg"
        img.write_bytes(b"x")
        geocoder = MagicMock(spec=ReverseGeocoder)
        geocoder.resolve.return_value = MagicMock(error=True)

        with ProgressDB(db_path=tmp_path / "test.db") as db:
            stored = ImageResult(
                file_path=str(img),
                file_name=img.name,
                tags=["sunset"],
                scene_summary="A sunset",
            )
            db.mark_done(img, stored)

            stats = {
                "skipped_date": 0,
                "skipped_no_gps": 0,
                "geocode_failures": 0,
                "resumed_from_db": 0,
            }
            with patch("pyimgtag.commands.run.read_exif") as mock_exif:
                mock_exif.return_value = ExifData()
                result = _hydrate_from_db(img, "directory", self._make_args(), geocoder, stats, db)

        assert result is not None
        assert "sunset" in result.tags
        assert stats["resumed_from_db"] == 1

    def test_exif_read_failure_sets_image_date_none(self, tmp_path):
        from pyimgtag.commands.run import _hydrate_from_db
        from pyimgtag.geocoder import ReverseGeocoder
        from pyimgtag.models import ImageResult
        from pyimgtag.progress_db import ProgressDB

        img = tmp_path / "photo.jpg"
        img.write_bytes(b"x")
        geocoder = MagicMock(spec=ReverseGeocoder)
        geocoder.resolve.return_value = MagicMock(error=True)

        with ProgressDB(db_path=tmp_path / "test.db") as db:
            stored = ImageResult(
                file_path=str(img),
                file_name=img.name,
                tags=["tree"],
            )
            db.mark_done(img, stored)

            stats = {
                "skipped_date": 0,
                "skipped_no_gps": 0,
                "geocode_failures": 0,
                "resumed_from_db": 0,
            }
            with patch("pyimgtag.commands.run.read_exif", side_effect=OSError("read fail")):
                result = _hydrate_from_db(img, "directory", self._make_args(), geocoder, stats, db)

        assert result is not None
        assert result.image_date is None


class TestSkipExisting:
    """--skip-existing fully skips unchanged photos already complete in the DB.

    No EXIF re-read, geocoding, AppleScript write-back, or DB rewrite — the
    fast path for resuming a large, mostly-tagged library.
    """

    def _seed_complete_row(self, db_path: Path, img: Path) -> None:
        from pyimgtag.models import ImageResult
        from pyimgtag.progress_db import ProgressDB

        with ProgressDB(db_path=db_path) as db:
            db.mark_done(
                img,
                ImageResult(
                    file_path=str(img),
                    file_name=img.name,
                    tags=["sunset", "beach"],
                    scene_summary="A sunny day",
                    processing_status="ok",
                ),
            )

    def _parse(self, tmp_path: Path, *, photos: bool, extra: list[str]) -> object:
        from pyimgtag.main import build_parser

        parser = build_parser()
        src = ["--photos-library", str(tmp_path)] if photos else ["--input-dir", str(tmp_path)]
        return parser.parse_args(
            [
                "run",
                *src,
                "--extensions",
                "jpg",
                "--no-web",
                "--db",
                str(tmp_path / "progress.db"),
                *extra,
            ]
        )

    def test_skip_existing_skips_complete_row_without_exif_or_ollama(self, tmp_path: Path) -> None:
        from pyimgtag.commands.run import cmd_run

        img = tmp_path / "photo.jpg"
        img.write_bytes(b"x")
        self._seed_complete_row(tmp_path / "progress.db", img)
        args = self._parse(tmp_path, photos=False, extra=["--skip-existing"])

        with (
            patch("pyimgtag.commands.run.check_ollama", return_value=(True, "")),
            patch("pyimgtag.commands.run.OllamaClient") as mock_client_cls,
            patch("pyimgtag.commands.run.scan_directory", return_value=[img]),
            patch("pyimgtag.commands.run.read_exif") as mock_exif,
        ):
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client
            rc = cmd_run(args, MagicMock())

        assert rc == 0
        mock_client.tag_image.assert_not_called()  # never re-tagged
        mock_exif.assert_not_called()  # no per-file exiftool subprocess

    def test_skip_existing_processes_uncached_file(self, tmp_path: Path) -> None:
        from pyimgtag.commands.run import cmd_run
        from pyimgtag.models import TagResult

        img = tmp_path / "fresh.jpg"
        img.write_bytes(b"x")  # no DB row
        args = self._parse(tmp_path, photos=False, extra=["--skip-existing"])

        with (
            patch("pyimgtag.commands.run.check_ollama", return_value=(True, "")),
            patch("pyimgtag.commands.run.OllamaClient") as mock_client_cls,
            patch("pyimgtag.commands.run.scan_directory", return_value=[img]),
            patch("pyimgtag.commands.run.read_exif", return_value=ExifData()),
        ):
            mock_client = MagicMock()
            mock_client.tag_image.return_value = TagResult(tags=["nature"], summary="")
            mock_client_cls.return_value = mock_client
            rc = cmd_run(args, MagicMock())

        assert rc == 0
        mock_client.tag_image.assert_called_once()

    def test_skip_existing_retags_changed_file(self, tmp_path: Path) -> None:
        from pyimgtag.commands.run import cmd_run
        from pyimgtag.models import TagResult

        img = tmp_path / "photo.jpg"
        img.write_bytes(b"x")
        self._seed_complete_row(tmp_path / "progress.db", img)
        img.write_bytes(b"changed-bytes-now-bigger")  # size/mtime differ from DB row
        args = self._parse(tmp_path, photos=False, extra=["--skip-existing"])

        with (
            patch("pyimgtag.commands.run.check_ollama", return_value=(True, "")),
            patch("pyimgtag.commands.run.OllamaClient") as mock_client_cls,
            patch("pyimgtag.commands.run.scan_directory", return_value=[img]),
            patch("pyimgtag.commands.run.read_exif", return_value=ExifData()),
        ):
            mock_client = MagicMock()
            mock_client.tag_image.return_value = TagResult(tags=["nature"], summary="")
            mock_client_cls.return_value = mock_client
            rc = cmd_run(args, MagicMock())

        assert rc == 0
        mock_client.tag_image.assert_called_once()  # changed file is re-tagged

    def test_skip_existing_no_writeback_for_complete_photo(self, tmp_path: Path) -> None:
        """The core perf guarantee: skipped photos trigger no AppleScript write-back."""
        from pyimgtag.commands.run import cmd_run

        img = tmp_path / "photo.jpg"
        img.write_bytes(b"x")
        self._seed_complete_row(tmp_path / "progress.db", img)
        args = self._parse(tmp_path, photos=True, extra=["--skip-existing", "--write-back"])

        with (
            patch("pyimgtag.commands.run.check_ollama", return_value=(True, "")),
            patch("pyimgtag.commands.run.OllamaClient") as mock_client_cls,
            patch("pyimgtag.commands.run.scan_photos_library", return_value=[img]),
            patch("pyimgtag.commands.run.read_exif") as mock_exif,
            patch("pyimgtag.applescript_writer.write_to_photos") as mock_write,
        ):
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client
            rc = cmd_run(args, MagicMock())

        assert rc == 0
        mock_client.tag_image.assert_not_called()
        mock_exif.assert_not_called()
        mock_write.assert_not_called()  # no per-photo osascript write-back

    def test_without_skip_existing_resume_still_reads_exif(self, tmp_path: Path) -> None:
        """Control: --resume-from-db (no --skip-existing) still hydrates (reads EXIF).

        Demonstrates that --skip-existing is what bypasses the expensive path.
        """
        from pyimgtag.commands.run import cmd_run

        img = tmp_path / "photo.jpg"
        img.write_bytes(b"x")
        self._seed_complete_row(tmp_path / "progress.db", img)
        args = self._parse(tmp_path, photos=False, extra=["--resume-from-db"])

        with (
            patch("pyimgtag.commands.run.check_ollama", return_value=(True, "")),
            patch("pyimgtag.commands.run.OllamaClient") as mock_client_cls,
            patch("pyimgtag.commands.run.scan_directory", return_value=[img]),
            patch("pyimgtag.commands.run.read_exif", return_value=ExifData()) as mock_exif,
        ):
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client
            rc = cmd_run(args, MagicMock())

        assert rc == 0
        mock_client.tag_image.assert_not_called()  # cached, not re-tagged
        mock_exif.assert_called_once()  # but EXIF IS re-read (the slow path)

    def test_skip_existing_is_noop_under_no_cache(self, tmp_path: Path) -> None:
        """--no-cache disables the DB, so --skip-existing cannot skip — file is tagged."""
        from pyimgtag.commands.run import cmd_run
        from pyimgtag.models import TagResult

        img = tmp_path / "photo.jpg"
        img.write_bytes(b"x")
        self._seed_complete_row(tmp_path / "progress.db", img)  # row exists but ignored
        args = self._parse(tmp_path, photos=False, extra=["--skip-existing", "--no-cache"])

        with (
            patch("pyimgtag.commands.run.check_ollama", return_value=(True, "")),
            patch("pyimgtag.commands.run.OllamaClient") as mock_client_cls,
            patch("pyimgtag.commands.run.scan_directory", return_value=[img]),
            patch("pyimgtag.commands.run.read_exif", return_value=ExifData()),
        ):
            mock_client = MagicMock()
            mock_client.tag_image.return_value = TagResult(tags=["nature"], summary="")
            mock_client_cls.return_value = mock_client
            rc = cmd_run(args, MagicMock())

        assert rc == 0
        mock_client.tag_image.assert_called_once()  # skip disabled with no DB

    def test_skip_existing_bypasses_threaded_resume_path(self, tmp_path: Path) -> None:
        """--skip-existing forces the linear path even with --resume-threaded.

        If the threaded re-hydration worker ran, it would call read_exif via
        _hydrate_from_db. Asserting read_exif is NOT called proves the linear
        skip path ran instead.
        """
        from pyimgtag.commands.run import cmd_run

        img = tmp_path / "photo.jpg"
        img.write_bytes(b"x")
        self._seed_complete_row(tmp_path / "progress.db", img)
        args = self._parse(
            tmp_path,
            photos=False,
            extra=["--skip-existing", "--resume-from-db", "--resume-threaded"],
        )

        with (
            patch("pyimgtag.commands.run.check_ollama", return_value=(True, "")),
            patch("pyimgtag.commands.run.OllamaClient") as mock_client_cls,
            patch("pyimgtag.commands.run.scan_directory", return_value=[img]),
            patch("pyimgtag.commands.run.read_exif") as mock_exif,
        ):
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client
            rc = cmd_run(args, MagicMock())

        assert rc == 0
        mock_client.tag_image.assert_not_called()
        mock_exif.assert_not_called()  # threaded hydration worker did not run

    def test_skip_existing_reports_count_in_summary(self, tmp_path: Path, capsys) -> None:
        """The skipped_existing counter is reflected in the run summary."""
        from pyimgtag.commands.run import cmd_run

        img = tmp_path / "photo.jpg"
        img.write_bytes(b"x")
        self._seed_complete_row(tmp_path / "progress.db", img)
        args = self._parse(tmp_path, photos=False, extra=["--skip-existing"])

        with (
            patch("pyimgtag.commands.run.check_ollama", return_value=(True, "")),
            patch("pyimgtag.commands.run.OllamaClient") as mock_client_cls,
            patch("pyimgtag.commands.run.scan_directory", return_value=[img]),
            patch("pyimgtag.commands.run.read_exif", return_value=ExifData()),
        ):
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client
            rc = cmd_run(args, MagicMock())

        assert rc == 0
        assert "Skipped (exists): 1" in capsys.readouterr().err

    def test_skip_existing_skips_before_keyword_read(self, tmp_path: Path) -> None:
        """A complete row is skipped before any Photos keyword read (--skip-if-tagged)."""
        from pyimgtag.commands.run import cmd_run

        img = tmp_path / "photo.jpg"
        img.write_bytes(b"x")
        self._seed_complete_row(tmp_path / "progress.db", img)
        args = self._parse(tmp_path, photos=True, extra=["--skip-existing", "--skip-if-tagged"])

        with (
            patch("pyimgtag.commands.run.check_ollama", return_value=(True, "")),
            patch("pyimgtag.commands.run.OllamaClient") as mock_client_cls,
            patch("pyimgtag.commands.run.scan_photos_library", return_value=[img]),
            patch("pyimgtag.commands.run.read_exif") as mock_exif,
            patch("pyimgtag.commands.run.read_keywords_from_photos") as mock_kw,
        ):
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client
            rc = cmd_run(args, MagicMock())

        assert rc == 0
        mock_client.tag_image.assert_not_called()
        mock_exif.assert_not_called()
        mock_kw.assert_not_called()  # no osascript keyword read for skipped photo
