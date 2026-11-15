"""``run`` reports the media files it leaves out because of their extension (#473)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from pyimgtag.main import build_parser
from pyimgtag.models import ExifData, TagResult
from pyimgtag.scanner import (
    DEFAULT_EXTENSIONS,
    DEFAULT_EXTENSIONS_ARG,
    scan_directory,
    scan_photos_library,
)


def _touch(folder: Path, *names: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        (folder / name).write_bytes(b"x")


class TestScannerReportsLeftOutMedia:
    def test_defaults_cover_webp_and_tiff(self):
        assert {"webp", "tif", "tiff"} <= DEFAULT_EXTENSIONS
        assert set(DEFAULT_EXTENSIONS_ARG.split(",")) == DEFAULT_EXTENSIONS

    def test_left_out_media_is_listed_and_other_files_are_not(self, tmp_path):
        _touch(
            tmp_path,
            "a.jpg",
            "b.webp",
            "c.tif",
            "d.CR2",
            "e.mp4",
            "a.xmp",
            "a.aae",
            "notes.txt",
            ".hidden.nef",
        )
        unsupported: list[Path] = []

        files = scan_directory(tmp_path, unsupported=unsupported)

        assert [p.name for p in files] == ["a.jpg", "b.webp", "c.tif"]
        assert [p.name for p in unsupported] == ["d.CR2", "e.mp4"]

    def test_an_added_extension_is_no_longer_reported(self, tmp_path):
        _touch(tmp_path, "a.jpg", "d.cr2")
        unsupported: list[Path] = []

        files = scan_directory(tmp_path, {"jpg", "cr2"}, unsupported=unsupported)

        assert len(files) == 2
        assert unsupported == []

    def test_without_a_list_nothing_changes(self, tmp_path):
        _touch(tmp_path, "a.jpg", "d.cr2")
        assert [p.name for p in scan_directory(tmp_path)] == ["a.jpg"]

    def test_non_recursive_scan_reports_only_the_top_level(self, tmp_path):
        _touch(tmp_path, "a.jpg", "top.cr2")
        _touch(tmp_path / "sub", "deep.cr2")
        unsupported: list[Path] = []

        scan_directory(tmp_path, recursive=False, unsupported=unsupported)

        assert [p.name for p in unsupported] == ["top.cr2"]

    def test_photos_library_reports_too(self, tmp_path):
        _touch(tmp_path / "originals" / "0", "a.heic", "b.gif")
        unsupported: list[Path] = []

        files = scan_photos_library(tmp_path, unsupported=unsupported)

        assert [p.name for p in files] == ["a.heic"]
        assert [p.name for p in unsupported] == ["b.gif"]


class TestRunSummary:
    def _run(self, tmp_path: Path, *extra: str) -> int:
        from pyimgtag.commands.run import cmd_run

        args = build_parser().parse_args(
            [
                "run",
                "--input-dir",
                str(tmp_path / "lib"),
                "--no-web",
                "--db",
                str(tmp_path / "progress.db"),
                *extra,
            ]
        )
        with (
            patch("pyimgtag.commands.run.check_ollama", return_value=(True, "")),
            patch("pyimgtag.commands.run.check_ollama_model", return_value=(True, "")),
            patch("pyimgtag.commands.run.OllamaClient") as mock_client_cls,
            patch("pyimgtag.commands.run.read_exif", return_value=ExifData()),
        ):
            mock_client = MagicMock()
            mock_client.tag_image.return_value = TagResult(tags=["nature"], summary="")
            mock_client_cls.return_value = mock_client
            return cmd_run(args, MagicMock())

    def test_summary_counts_and_names_the_skipped_file(self, tmp_path, capsys):
        _touch(tmp_path / "lib", "photo.jpg", "raw shot.NEF")

        assert self._run(tmp_path) == 0

        err = capsys.readouterr().err
        assert "Skipped (ext):     1" in err
        assert "Info: skipped 1 file(s)" in err
        assert "raw shot.NEF" in err
        assert "--extensions" in err

    def test_a_video_points_at_include_video(self, tmp_path, capsys):
        _touch(tmp_path / "lib", "photo.jpg", "clip.mp4")

        assert self._run(tmp_path) == 0

        err = capsys.readouterr().err
        assert "clip.mp4" in err
        assert "--include-video" in err

    def test_nothing_skipped_prints_no_info_line(self, tmp_path, capsys):
        _touch(tmp_path / "lib", "photo.jpg", "photo.xmp")

        assert self._run(tmp_path) == 0

        err = capsys.readouterr().err
        assert "Skipped (ext):     0" in err
        assert "whose extension is not scanned" not in err

    def test_only_unsupported_files_still_says_why(self, tmp_path, capsys):
        _touch(tmp_path / "lib", "a.cr2")

        assert self._run(tmp_path) == 0

        err = capsys.readouterr().err
        assert "No image files found." in err
        assert "a.cr2" in err

    def test_run_defaults_match_judge_and_index(self):
        parser = build_parser()
        run = parser.parse_args(["run", "--input-dir", "."])
        judge = parser.parse_args(["judge", "--input-dir", "."])
        assert run.extensions == judge.extensions == DEFAULT_EXTENSIONS_ARG
