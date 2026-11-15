"""`run` says what it wrote: --write-exif, --sidecar-only, --write-back (#438)."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import patch

import pytest

from pyimgtag.commands.run import _finalize_result, _new_stats, _print_summary
from pyimgtag.models import ImageResult


def _args(**kw) -> argparse.Namespace:
    values = dict(
        write_back=False,
        dry_run=False,
        write_back_mode="overwrite",
        write_exif=False,
        sidecar_only=False,
        verbose=False,
        jsonl_stdout=False,
        limit=None,
        metadata_format="auto",
        hierarchical_keywords=False,
    )
    values.update(kw)
    return argparse.Namespace(**values)


def _result(path: Path, source_type: str = "directory") -> ImageResult:
    path.write_bytes(b"x")
    return ImageResult(
        file_path=str(path), file_name=path.name, source_type=source_type, tags=["beach"]
    )


def _finalize(results, args, stats):
    for r in results:
        _finalize_result(r, Path(r.file_path), args, None, {}, [], stats)


def test_two_exif_writes_are_counted_and_shown(tmp_path, capsys):
    stats = _new_stats(2)
    args = _args(write_exif=True)
    with patch("pyimgtag.exif_writer.write_exif_description", return_value=None):
        _finalize([_result(tmp_path / "a.jpg"), _result(tmp_path / "b.jpg")], args, stats)
    _print_summary(stats, args)
    captured = capsys.readouterr()
    assert (captured.out + captured.err).count("| exif") == 2
    assert "  Metadata written:  2" in captured.err
    assert "  Write failures:    0" in captured.err


def test_sidecar_only_counts_sidecars(tmp_path, capsys):
    stats = _new_stats(2)
    args = _args(sidecar_only=True)
    with patch("pyimgtag.exif_writer.write_xmp_sidecar", return_value=None):
        _finalize([_result(tmp_path / "a.jpg"), _result(tmp_path / "b.jpg")], args, stats)
    _print_summary(stats, args)
    captured = capsys.readouterr()
    assert (captured.out + captured.err).count("| sidecar") == 2
    assert "  Sidecars written:  2" in captured.err
    assert "Metadata written" not in captured.err


def test_an_unsupported_format_falls_back_to_a_sidecar_and_says_so(tmp_path, capsys):
    stats = _new_stats(1)
    args = _args(write_exif=True)
    with patch("pyimgtag.exif_writer.write_xmp_sidecar", return_value=None):
        _finalize([_result(tmp_path / "a.xyz")], args, stats)
    assert stats["sidecars_written"] == 1 and stats["metadata_written"] == 0
    captured = capsys.readouterr()
    assert "| sidecar" in captured.out + captured.err


def test_a_failed_write_is_counted_and_not_marked(tmp_path, capsys):
    stats = _new_stats(1)
    args = _args(write_exif=True)
    with patch("pyimgtag.exif_writer.write_exif_description", return_value="exiftool exploded"):
        _finalize([_result(tmp_path / "a.jpg")], args, stats)
    _print_summary(stats, args)
    captured = capsys.readouterr()
    assert "| exif" not in captured.out + captured.err
    assert "  Metadata written:  0" in captured.err
    assert "  Write failures:    1" in captured.err


def test_dry_run_writes_nothing_and_the_counter_is_zero(tmp_path, capsys):
    stats = _new_stats(2)
    args = _args(write_exif=True, dry_run=True)
    with patch("pyimgtag.exif_writer.write_exif_description") as write:
        _finalize([_result(tmp_path / "a.jpg"), _result(tmp_path / "b.jpg")], args, stats)
    write.assert_not_called()
    _print_summary(stats, args)
    assert "  Metadata written:  0" in capsys.readouterr().err


@pytest.mark.parametrize(("error", "written", "failed"), [(None, 1, 0), ("no access", 0, 1)])
def test_write_back_success_and_failure_are_told_apart(tmp_path, capsys, error, written, failed):
    stats = _new_stats(1)
    args = _args(write_back=True)
    with patch("pyimgtag.applescript_writer.write_to_photos", return_value=error):
        _finalize([_result(tmp_path / "a.jpg", source_type="photos_library")], args, stats)
    _print_summary(stats, args)
    captured = capsys.readouterr()
    assert (stats["written_back"], stats["write_back_failures"]) == (written, failed)
    assert f"  Written back:      {written}" in captured.err
    assert f"  Photos failures:   {failed}" in captured.err
    assert ("| photos" in captured.out + captured.err) is bool(written)


def test_the_default_summary_is_unchanged_without_write_flags(capsys):
    _print_summary(_new_stats(3), _args())
    err = capsys.readouterr().err
    for label in ("Metadata written", "Sidecars written", "Written back", "Write failures"):
        assert label not in err


def test_verbose_output_names_the_writes(tmp_path, capsys):
    stats = _new_stats(1)
    args = _args(write_exif=True, verbose=True)
    with patch("pyimgtag.exif_writer.write_exif_description", return_value=None):
        _finalize([_result(tmp_path / "a.jpg")], args, stats)
    assert "  Written:  exif" in capsys.readouterr().err


def test_write_back_and_write_exif_together_mark_both(tmp_path, capsys):
    stats = _new_stats(1)
    args = _args(write_back=True, write_exif=True)
    with (
        patch("pyimgtag.applescript_writer.write_to_photos", return_value=None),
        patch("pyimgtag.exif_writer.write_exif_description", return_value=None),
    ):
        _finalize([_result(tmp_path / "a.jpg", source_type="photos_library")], args, stats)
    captured = capsys.readouterr()
    assert "| photos + exif" in captured.out + captured.err
    assert (stats["written_back"], stats["metadata_written"]) == (1, 1)


def test_a_watch_cycle_reports_its_writes(tmp_path, monkeypatch, capsys):
    """watch --write-exif logs how many files each cycle wrote."""
    from pyimgtag.commands import watch as watch_mod

    stats = _new_stats(2)
    stats.update(processed=2, metadata_written=1, metadata_write_failures=1)
    lines: list[str] = []
    monkeypatch.setattr(watch_mod, "_log", lines.append)

    class _Session:
        def set_counter(self, *_a):
            pass

        def set_current(self, *_a):
            pass

        def is_stop_requested(self):
            return False

    watcher = watch_mod._Watcher.__new__(watch_mod._Watcher)
    watcher.args = _args(
        write_exif=True, max_cycles=1, input_dir=str(tmp_path), photos_library=None
    )
    watcher.cycles = 0
    watcher.session = _Session()
    watcher.interrupted = False
    watcher.stop = __import__("threading").Event()
    watcher.extensions = {"jpg"}
    monkeypatch.setattr(
        watch_mod._run, "_scan_files", lambda *_a: ("directory", [tmp_path / "a.jpg"])
    )
    monkeypatch.setattr(watcher, "_candidates", lambda files: (files, 0), raising=False)
    monkeypatch.setattr(watcher, "_tag", lambda *_a: stats, raising=False)
    watcher.cycle()
    assert "written 1 · write failures 1" in lines[-1]
