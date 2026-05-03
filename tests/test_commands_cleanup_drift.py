"""Tests for the ``cleanup-drift`` CLI subcommand.

These tests stay platform-agnostic: the AppleScript probe is mocked
through the ``fetch_membership`` seam exposed by
:func:`pyimgtag.cleanup_drift.scan_drift`, so the suite never shells
out to ``osascript`` and runs identically on Linux CI runners.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from pyimgtag.cleanup_drift import (
    CAT_DISK_MISSING,
    CAT_PHOTOS_MISSING,
    CAT_PRESENT,
    DriftReport,
    _classify,
    prune_drift,
    scan_drift,
)
from pyimgtag.commands.cleanup_drift import cmd_cleanup_drift
from pyimgtag.models import ImageResult
from pyimgtag.progress_db import ProgressDB


def _seed(db_path: Path, tmp_path: Path) -> tuple[str, str, str]:
    """Insert three rows: present, disk_missing, photos_missing.

    Returns (present_path, disk_missing_path, photos_missing_path) so
    individual tests can assert which row got pruned.
    """
    present = tmp_path / "present.jpg"
    present.write_bytes(b"\x00")  # exists on disk
    photos_missing = tmp_path / "photos_missing.jpg"
    photos_missing.write_bytes(b"\x00")  # also on disk, just absent from Photos
    disk_missing = tmp_path / "disk_missing.jpg"
    disk_missing.write_bytes(b"\x00")  # we delete this below

    with ProgressDB(db_path=db_path) as db:
        for p in (present, photos_missing, disk_missing):
            db.mark_done(
                p,
                ImageResult(file_path=str(p), file_name=p.name, processing_status="ok"),
            )

    # Now nuke the disk_missing file so the scanner sees the dead row.
    disk_missing.unlink()

    return str(present), str(disk_missing), str(photos_missing)


def _membership_factory(present_paths: list[str]) -> callable:
    """Return a fake ``fetch_membership`` that exposes only *present_paths*."""
    membership = {Path(p).name for p in present_paths}

    def _fake() -> tuple[set[str], str | None]:
        return membership, None

    return _fake


class TestClassify:
    def test_disk_missing_overrides_photos(self, tmp_path: Path) -> None:
        # File doesn't exist on disk — disk_missing always wins, even
        # if the (mock) Photos.app would report it as present.
        gone = tmp_path / "gone.jpg"
        assert _classify(str(gone), {"gone.jpg"}) == CAT_DISK_MISSING

    def test_photos_missing_when_membership_lacks_filename(self, tmp_path: Path) -> None:
        on_disk = tmp_path / "still.jpg"
        on_disk.write_bytes(b"x")
        assert _classify(str(on_disk), set()) == CAT_PHOTOS_MISSING

    def test_present_when_filename_in_membership(self, tmp_path: Path) -> None:
        on_disk = tmp_path / "still.jpg"
        on_disk.write_bytes(b"x")
        assert _classify(str(on_disk), {"still.jpg"}) == CAT_PRESENT

    def test_present_when_membership_unavailable(self, tmp_path: Path) -> None:
        # ``None`` membership = degraded probe; on-disk rows collapse
        # into ``present`` so the prune step is conservative.
        on_disk = tmp_path / "still.jpg"
        on_disk.write_bytes(b"x")
        assert _classify(str(on_disk), None) == CAT_PRESENT

    def test_uuid_stem_resolves_via_membership(self, tmp_path: Path) -> None:
        uuid_name = "0110B5A5-C112-4F30-A21D-CBB99BBA3985.jpg"
        on_disk = tmp_path / uuid_name
        on_disk.write_bytes(b"x")
        # Photos can expose either the UUID stem or the filename. The
        # classifier accepts both spellings.
        stem = uuid_name.split(".")[0]
        assert _classify(str(on_disk), {stem}) == CAT_PRESENT


class TestScanDrift:
    def test_classifies_three_categories(self, tmp_path: Path) -> None:
        db_path = tmp_path / "drift.db"
        present, disk_missing, photos_missing = _seed(db_path, tmp_path)
        with ProgressDB(db_path=db_path) as db:
            report = scan_drift(db, fetch_membership=_membership_factory([present]))

        assert report.total == 3
        assert report.present == 1
        assert report.disk_missing == 1
        assert report.photos_missing == 1
        assert sorted(report.dead_paths) == sorted([disk_missing, photos_missing])

    def test_probe_error_collapses_photos_missing(self, tmp_path: Path) -> None:
        db_path = tmp_path / "drift.db"
        _, disk_missing, _ = _seed(db_path, tmp_path)

        def _broken() -> tuple[set[str], str | None]:
            return set(), "parse_error"

        with ProgressDB(db_path=db_path) as db:
            report = scan_drift(db, fetch_membership=_broken)

        assert report.photos_probe_error == "parse_error"
        # Without a usable membership map, only ``disk_missing`` rows
        # are detectable; the other two collapse into ``present``.
        assert report.disk_missing == 1
        assert report.photos_missing == 0
        assert report.present == 2
        assert report.dead_paths == [disk_missing]


class TestPruneDrift:
    def test_prune_removes_only_dead_paths(self, tmp_path: Path) -> None:
        db_path = tmp_path / "drift.db"
        present, disk_missing, photos_missing = _seed(db_path, tmp_path)
        with ProgressDB(db_path=db_path) as db:
            removed = prune_drift(db, [disk_missing, photos_missing])
            assert removed == 2
            remaining = sorted(db.iter_image_paths())
            assert remaining == [present]


class TestCmdCleanupDrift:
    def _ns(self, db_path: Path, *, prune: bool = False) -> argparse.Namespace:
        return argparse.Namespace(db=str(db_path), dry_run=not prune, prune=prune)

    def test_dry_run_does_not_delete(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db_path = tmp_path / "drift.db"
        present, disk_missing, photos_missing = _seed(db_path, tmp_path)

        # Patch the bulk Photos probe so the test stays platform-agnostic.
        monkeypatch.setattr(
            "pyimgtag.cleanup_drift.fetch_photos_membership",
            _membership_factory([present]),
        )

        rc = cmd_cleanup_drift(self._ns(db_path, prune=False))
        assert rc == 0
        captured = capsys.readouterr()
        assert "would delete" in captured.out
        assert "3 rows in DB" in captured.out
        assert "2 with missing file" in captured.out

        # DB still has all three rows.
        with ProgressDB(db_path=db_path) as db:
            assert db.count_images() == 3

    def test_prune_deletes_dead_rows(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db_path = tmp_path / "drift.db"
        present, disk_missing, photos_missing = _seed(db_path, tmp_path)
        monkeypatch.setattr(
            "pyimgtag.cleanup_drift.fetch_photos_membership",
            _membership_factory([present]),
        )

        rc = cmd_cleanup_drift(self._ns(db_path, prune=True))
        assert rc == 0
        captured = capsys.readouterr()
        assert "deleted" in captured.out
        assert "2 deleted" in captured.out

        with ProgressDB(db_path=db_path) as db:
            remaining = sorted(db.iter_image_paths())
            assert remaining == [present]

    def test_probe_error_degrades_gracefully(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A ``-2741`` parse failure must still let the disk-only check run."""
        db_path = tmp_path / "drift.db"
        _, disk_missing, _ = _seed(db_path, tmp_path)

        def _broken() -> tuple[set[str], str | None]:
            return set(), "parse_error"

        monkeypatch.setattr("pyimgtag.cleanup_drift.fetch_photos_membership", _broken)

        rc = cmd_cleanup_drift(self._ns(db_path, prune=True))
        assert rc == 0
        captured = capsys.readouterr()
        # Only the disk_missing row should have been pruned — the
        # photos_missing classification cannot be inferred here.
        with ProgressDB(db_path=db_path) as db:
            remaining = sorted(db.iter_image_paths())
            assert disk_missing not in remaining
            assert len(remaining) == 2
        assert "Photos.app probe degraded" in captured.err


class TestDriftReport:
    def test_sample_clipped_to_n(self) -> None:
        report = DriftReport(dead_paths=[f"/p/{i}.jpg" for i in range(50)])
        assert len(report.sample(20)) == 20
        assert report.sample(20)[0] == "/p/0.jpg"

    def test_dead_count_sums_disk_and_photos(self) -> None:
        report = DriftReport(disk_missing=3, photos_missing=4)
        assert report.dead_count == 7
