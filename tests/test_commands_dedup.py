"""Tests for the ``pyimgtag dedup`` subcommand family."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from pyimgtag.commands.dedup import cmd_dedup
from pyimgtag.db.dedup_db import ACTION_MOVE, ACTION_TAG, ACTION_TRASH
from pyimgtag.main import build_parser
from pyimgtag.models import ImageResult
from pyimgtag.progress_db import ProgressDB

HASH_A = "ffffffffffffffff"
HASH_A_NEAR = "fffffffffffffff0"


def _args(*argv):
    return build_parser().parse_args(["dedup", *argv])


def _seed_row(db: ProgressDB, path: Path, phash: str | None, size: int | None = 100) -> str:
    """Create the file (unless ``size`` is None) and record it in the DB."""
    if size is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)
    db.mark_done(
        path,
        ImageResult(
            file_path=str(path),
            file_name=path.name,
            processing_status="ok",
            phash=phash,
        ),
    )
    return str(path)


def _seeded_group(tmp_path: Path) -> tuple[Path, str, str]:
    """A DB with one two-member duplicate group; returns (db_path, small, big)."""
    db_path = tmp_path / "progress.db"
    db = ProgressDB(db_path=db_path)
    small_path = tmp_path / "lib" / "small.jpg"
    small_path.parent.mkdir(parents=True, exist_ok=True)
    small_path.write_bytes(b"x" * 100)
    # Pin the mtimes so the "oldest wins" tie-break is deterministic on every
    # filesystem (some have 1-second timestamp granularity).
    os.utime(small_path, (1_000_000, 1_000_000))
    small = _seed_row(db, small_path, HASH_A, size=None)
    big_path = tmp_path / "lib" / "big.jpg"
    big_path.write_bytes(b"x" * 900)
    os.utime(big_path, (2_000_000, 2_000_000))
    big = _seed_row(db, big_path, HASH_A_NEAR, size=None)
    db.replace_unresolved_dedup_groups([("duplicate", [small, big])], 5)
    db.close()
    return db_path, small, big


def _tiny_image(path: Path, color: tuple[int, int, int], size=(64, 64)) -> None:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)


# --- parser ----------------------------------------------------------------


def test_parser_defaults():
    args = _args("scan")
    assert args.dedup_action == "scan"
    assert args.threshold == 5
    assert args.rehash is False


def test_parser_resolve_flags():
    args = _args("resolve", "--move-to", "/q", "--prefer", "size", "--group", "3")
    assert args.move_to == "/q"
    assert args.prefer == "size"
    assert args.group == 3
    assert args.delete is False


def test_parser_rejects_move_to_with_delete():
    with pytest.raises(SystemExit):
        _args("resolve", "--move-to", "/q", "--delete")


def test_main_rejects_an_unknown_prefer_criterion(capsys):
    from pyimgtag.main import main

    with pytest.raises(SystemExit):
        main(["dedup", "list", "--prefer", "bogus"])
    assert "unknown ranking criterion" in capsys.readouterr().err


def test_dispatch_without_an_action_prints_usage(capsys):
    args = build_parser().parse_args(["dedup"])
    assert cmd_dedup(args) == 1
    assert "Usage: pyimgtag dedup" in capsys.readouterr().err


def test_dispatch_rejects_an_unknown_action(capsys):
    args = _args("scan")
    args.dedup_action = "nope"
    assert cmd_dedup(args) == 1
    assert "Unknown dedup action" in capsys.readouterr().err


# --- scan ------------------------------------------------------------------


def test_scan_hashes_images_and_builds_groups(tmp_path, capsys):
    db_path = tmp_path / "progress.db"
    db = ProgressDB(db_path=db_path)
    first = tmp_path / "a.jpg"
    second = tmp_path / "b.jpg"
    _tiny_image(first, (10, 120, 200))
    _tiny_image(second, (10, 120, 200))
    for path in (first, second):
        db.mark_done(
            path,
            ImageResult(file_path=str(path), file_name=path.name, processing_status="ok"),
        )
    db.close()

    args = _args("scan")
    args.db = str(db_path)
    assert cmd_dedup(args) == 0
    out = capsys.readouterr().out
    assert "Hashed 2 image(s)" in out
    assert "1 unresolved group(s)" in out

    db = ProgressDB(db_path=db_path)
    groups = db.list_dedup_groups()
    assert len(groups) == 1
    assert {m["file_path"] for m in groups[0]["members"]} == {str(first), str(second)}
    assert all(m["width"] == 64 and m["height"] == 64 for m in groups[0]["members"])
    db.close()


def test_scan_counts_rows_whose_file_is_gone(tmp_path, capsys):
    db_path = tmp_path / "progress.db"
    db = ProgressDB(db_path=db_path)
    ghost = tmp_path / "ghost.jpg"
    ghost.write_bytes(b"x")
    db.mark_done(
        ghost, ImageResult(file_path=str(ghost), file_name=ghost.name, processing_status="ok")
    )
    ghost.unlink()
    db.close()

    args = _args("scan")
    args.db = str(db_path)
    assert cmd_dedup(args) == 0
    assert "Skipped 1 row(s) whose file is missing" in capsys.readouterr().out


def test_scan_counts_undecodable_rows(tmp_path, capsys):
    db_path = tmp_path / "progress.db"
    db = ProgressDB(db_path=db_path)
    junk = tmp_path / "junk.jpg"
    junk.write_bytes(b"not an image")
    db.mark_done(
        junk, ImageResult(file_path=str(junk), file_name=junk.name, processing_status="ok")
    )
    db.close()

    args = _args("scan")
    args.db = str(db_path)
    assert cmd_dedup(args) == 0
    assert "could not be decoded" in capsys.readouterr().out


# --- list ------------------------------------------------------------------


def test_list_table_shows_the_best_pick(tmp_path, capsys):
    db_path, small, big = _seeded_group(tmp_path)
    args = _args("list")
    args.db = str(db_path)
    assert cmd_dedup(args) == 0
    out = capsys.readouterr().out
    assert "BEST PICK" in out
    assert big in out
    assert "1 group(s)" in out


def test_list_json_carries_the_plan(tmp_path, capsys):
    db_path, small, big = _seeded_group(tmp_path)
    args = _args("list", "--format", "json")
    args.db = str(db_path)
    assert cmd_dedup(args) == 0
    doc = json.loads(capsys.readouterr().out)
    group = doc["groups"][0]
    assert group["best_path"] == big
    assert group["reclaimable_bytes"] == 100
    assert "size" in group["best_reasons"]


def test_list_prefer_changes_the_best_pick(tmp_path, capsys):
    db_path, small, big = _seeded_group(tmp_path)
    args = _args("list", "--format", "json", "--prefer", "mtime")
    args.db = str(db_path)
    assert cmd_dedup(args) == 0
    doc = json.loads(capsys.readouterr().out)
    # ``small`` was written first, so oldest-mtime wins for it.
    assert doc["groups"][0]["best_path"] == small


def test_list_reports_an_empty_plan(tmp_path, capsys):
    args = _args("list")
    args.db = str(tmp_path / "empty.db")
    assert cmd_dedup(args) == 0
    assert "No duplicate groups" in capsys.readouterr().err


def test_list_rejects_a_bad_prefer_value(tmp_path, capsys):
    args = _args("list")
    args.db = str(tmp_path / "empty.db")
    args.prefer = "nope"
    assert cmd_dedup(args) == 2
    assert "unknown ranking criterion" in capsys.readouterr().err


# --- resolve ---------------------------------------------------------------


def test_resolve_without_an_action_is_report_only(tmp_path, capsys):
    db_path, small, big = _seeded_group(tmp_path)
    before = {p: Path(p).read_bytes() for p in (small, big)}
    args = _args("resolve")
    args.db = str(db_path)
    assert cmd_dedup(args) == 0
    out = capsys.readouterr().out
    assert "[plan]" in out
    assert "Nothing was changed." in out
    assert {p: Path(p).read_bytes() for p in (small, big)} == before

    db = ProgressDB(db_path=db_path)
    group = db.list_dedup_groups()[0]
    assert group["resolved_at"] is None
    assert all(m["action"] is None for m in group["members"])
    db.close()


def test_resolve_dry_run_with_move_to_touches_nothing(tmp_path, capsys):
    db_path, small, big = _seeded_group(tmp_path)
    quarantine = tmp_path / "q"
    args = _args("resolve", "--move-to", str(quarantine), "--dry-run")
    args.db = str(db_path)
    assert cmd_dedup(args) == 0
    assert "[plan]" in capsys.readouterr().out
    assert Path(small).exists()
    assert not quarantine.exists()

    db = ProgressDB(db_path=db_path)
    assert db.list_dedup_groups()[0]["resolved_at"] is None
    db.close()


def test_resolve_move_to_and_undo_round_trip(tmp_path, capsys):
    db_path, small, big = _seeded_group(tmp_path)
    quarantine = tmp_path / "quarantine"

    args = _args("resolve", "--move-to", str(quarantine))
    args.db = str(db_path)
    assert cmd_dedup(args) == 0
    assert "Resolved 1 group(s)" in capsys.readouterr().out

    # The loser moved, structure preserved; the keeper stayed put.
    assert not Path(small).exists()
    assert Path(big).exists()
    moved = quarantine.joinpath(*Path(small).parts[1:])
    assert moved.exists()

    db = ProgressDB(db_path=db_path)
    group = db.list_dedup_groups(include_resolved=True)[0]
    assert group["resolved_at"] is not None
    assert group["keep_path"] == big
    loser = next(m for m in group["members"] if m["file_path"] == small)
    assert loser["action"] == ACTION_MOVE
    assert loser["moved_to"] == str(moved)
    db.close()

    undo = _args("undo")
    undo.db = str(db_path)
    assert cmd_dedup(undo) == 0
    assert "Restored 1 file(s)" in capsys.readouterr().out
    assert Path(small).exists()
    assert not moved.exists()

    db = ProgressDB(db_path=db_path)
    group = db.list_dedup_groups()[0]
    assert group["resolved_at"] is None
    assert all(m["action"] is None for m in group["members"])
    db.close()


def test_resolve_only_the_requested_group(tmp_path, capsys):
    db_path, small, big = _seeded_group(tmp_path)
    args = _args("resolve", "--group", "999")
    args.db = str(db_path)
    assert cmd_dedup(args) == 1
    assert "No unresolved group with id 999" in capsys.readouterr().err


def test_resolve_reports_an_empty_plan(tmp_path, capsys):
    args = _args("resolve")
    args.db = str(tmp_path / "empty.db")
    assert cmd_dedup(args) == 0
    assert "No duplicate groups" in capsys.readouterr().err


def test_resolve_delete_requires_yes(tmp_path, capsys):
    db_path, small, big = _seeded_group(tmp_path)
    args = _args("resolve", "--delete")
    args.db = str(db_path)
    assert cmd_dedup(args) == 2
    assert "--delete requires --yes" in capsys.readouterr().err
    assert Path(small).exists()


def test_resolve_delete_uses_send2trash(tmp_path, monkeypatch, capsys):
    db_path, small, big = _seeded_group(tmp_path)
    trashed: list[str] = []
    monkeypatch.setitem(sys.modules, "send2trash", type(sys)("send2trash"))
    sys.modules["send2trash"].send2trash = trashed.append  # type: ignore[attr-defined]
    monkeypatch.setattr("os.remove", _fail_on_remove)

    args = _args("resolve", "--delete", "--yes")
    args.db = str(db_path)
    assert cmd_dedup(args) == 0
    assert trashed == [small]
    # send2trash is mocked, so the file is still on disk — what matters is that
    # nothing else removed it.
    assert Path(small).exists()

    db = ProgressDB(db_path=db_path)
    group = db.list_dedup_groups(include_resolved=True)[0]
    loser = next(m for m in group["members"] if m["file_path"] == small)
    assert loser["action"] == ACTION_TRASH
    db.close()


def _fail_on_remove(*_args, **_kwargs):  # pragma: no cover — guard, never called
    raise AssertionError("pyimgtag must never call os.remove on a photo")


def test_resolve_delete_without_send2trash_reports_the_extra(tmp_path, monkeypatch, capsys):
    db_path, small, big = _seeded_group(tmp_path)
    monkeypatch.setitem(sys.modules, "send2trash", None)

    args = _args("resolve", "--delete", "--yes")
    args.db = str(db_path)
    assert cmd_dedup(args) == 1
    assert "pyimgtag[dedup]" in capsys.readouterr().err
    assert Path(small).exists()


def test_undo_after_trash_warns_that_it_cannot_restore(tmp_path, capsys):
    db_path, small, big = _seeded_group(tmp_path)
    db = ProgressDB(db_path=db_path)
    group_id = db.list_dedup_groups()[0]["id"]
    db.record_dedup_action(group_id, small, ACTION_TRASH)
    db.mark_dedup_resolved(group_id, big)
    db.close()

    args = _args("undo")
    args.db = str(db_path)
    assert cmd_dedup(args) == 0
    err = capsys.readouterr().err
    assert "restored" in err.lower()


def test_undo_with_nothing_to_undo(tmp_path, capsys):
    db_path, _small, _big = _seeded_group(tmp_path)
    args = _args("undo")
    args.db = str(db_path)
    assert cmd_dedup(args) == 0
    assert "Nothing to undo" in capsys.readouterr().err


def test_undo_unknown_group(tmp_path, capsys):
    db_path, _small, _big = _seeded_group(tmp_path)
    args = _args("undo", "--group", "42")
    args.db = str(db_path)
    assert cmd_dedup(args) == 1
    assert "No resolved group with id 42" in capsys.readouterr().err


def test_undo_keeps_the_record_when_the_quarantined_file_is_gone(tmp_path, capsys):
    db_path, small, big = _seeded_group(tmp_path)
    db = ProgressDB(db_path=db_path)
    group_id = db.list_dedup_groups()[0]["id"]
    db.record_dedup_action(group_id, small, ACTION_MOVE, str(tmp_path / "gone" / "small.jpg"))
    db.mark_dedup_resolved(group_id, big)
    db.close()

    args = _args("undo")
    args.db = str(db_path)
    assert cmd_dedup(args) == 1
    assert "missing quarantined file" in capsys.readouterr().err

    db = ProgressDB(db_path=db_path)
    assert db.get_dedup_group(group_id)["resolved_at"] is not None
    db.close()


# --- Apple Photos originals -------------------------------------------------


def _photos_group(tmp_path: Path) -> tuple[Path, str, str]:
    db_path = tmp_path / "progress.db"
    db = ProgressDB(db_path=db_path)
    original = _seed_row(
        db, tmp_path / "Photos.photoslibrary" / "originals" / "a.jpg", HASH_A, size=100
    )
    export = _seed_row(db, tmp_path / "export" / "a.jpg", HASH_A_NEAR, size=900)
    db.replace_unresolved_dedup_groups([("duplicate", [original, export])], 5)
    db.close()
    return db_path, original, export


def test_resolve_never_moves_a_photos_library_original(tmp_path, capsys):
    db_path, original, export = _photos_group(tmp_path)
    quarantine = tmp_path / "q"
    args = _args("resolve", "--move-to", str(quarantine))
    args.db = str(db_path)
    assert cmd_dedup(args) == 0
    out = capsys.readouterr().out
    assert "never touched on disk" in out
    assert Path(original).exists()
    assert not quarantine.exists()

    db = ProgressDB(db_path=db_path)
    group = db.list_dedup_groups(include_resolved=True)[0]
    member = next(m for m in group["members"] if m["file_path"] == original)
    assert member["action"] == ACTION_TAG
    db.close()


def test_resolve_delete_never_trashes_a_photos_library_original(tmp_path, monkeypatch, capsys):
    db_path, original, export = _photos_group(tmp_path)
    trashed: list[str] = []
    monkeypatch.setitem(sys.modules, "send2trash", type(sys)("send2trash"))
    sys.modules["send2trash"].send2trash = trashed.append  # type: ignore[attr-defined]

    args = _args("resolve", "--delete", "--yes")
    args.db = str(db_path)
    assert cmd_dedup(args) == 0
    assert trashed == []
    assert Path(original).exists()


def test_resolve_write_back_calls_applescript(tmp_path, monkeypatch, capsys):
    db_path, original, export = _photos_group(tmp_path)
    calls: list[tuple] = []

    def fake_write(file_path, tags, summary, mode="overwrite"):
        calls.append((file_path, tuple(tags), mode))
        return None

    # Patch the consumer-side seam rather than the applescript_writer module
    # attribute: CI showed the real function still being resolved on Linux.
    monkeypatch.setattr("pyimgtag.commands.dedup._photos_writer", lambda: fake_write)
    monkeypatch.setattr(sys, "platform", "darwin")

    args = _args("resolve", "--move-to", str(tmp_path / "q"), "--write-back")
    args.db = str(db_path)
    assert cmd_dedup(args) == 0
    assert calls == [(original, ("pyimgtag:duplicate",), "append")]


def test_resolve_write_back_off_platform_is_reported(tmp_path, monkeypatch, capsys):
    db_path, original, export = _photos_group(tmp_path)
    monkeypatch.setattr(sys, "platform", "linux")

    args = _args("resolve", "--move-to", str(tmp_path / "q"), "--write-back")
    args.db = str(db_path)
    assert cmd_dedup(args) == 1
    assert "macOS only" in capsys.readouterr().err
