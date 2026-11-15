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
    ProgressDB(args.db).close()  # read-only commands need an existing --db
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
    assert "No resolved or partly applied group with id 42" in capsys.readouterr().err


def test_undo_reports_a_copy_lost_from_both_places_and_unlocks_the_group(tmp_path, capsys):
    """Neither the quarantined copy nor the original exists: report it, then
    forget the record so the group is not locked out of every later scan."""
    db_path, small, big = _seeded_group(tmp_path)
    db = ProgressDB(db_path=db_path)
    group_id = db.list_dedup_groups()[0]["id"]
    db.record_dedup_action(group_id, small, ACTION_MOVE, str(tmp_path / "gone" / "small.jpg"))
    db.mark_dedup_resolved(group_id, big)
    db.close()
    Path(small).unlink()
    (tmp_path / "gone").mkdir()  # the quarantine is reachable; the copy is not in it

    args = _args("undo")
    args.db = str(db_path)
    assert cmd_dedup(args) == 1
    assert "lost: neither" in capsys.readouterr().err

    db = ProgressDB(db_path=db_path)
    group = db.get_dedup_group(group_id)
    assert group["resolved_at"] is None
    assert all(m["action"] is None for m in group["members"])
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


# --- #450: a resolve that stops half-way stays undoable ----------------------


def _four_copies(tmp_path: Path) -> tuple[Path, list[str]]:
    """One group: the big keeper plus three smaller copies (one Cyrillic)."""
    db_path = tmp_path / "progress.db"
    db = ProgressDB(db_path=db_path)
    paths = []
    for i, (name, size) in enumerate(
        (("keep.jpg", 900), ("a.jpg", 100), ("b.jpg", 100), ("закат.jpg", 100))
    ):
        p = tmp_path / "lib" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * size)
        os.utime(p, (1_000_000 + i, 1_000_000 + i))
        paths.append(_seed_row(db, p, HASH_A, size=None))
    db.replace_unresolved_dedup_groups([("duplicate", paths)], 5)
    db.close()
    return db_path, paths


def _undo(db_path: Path) -> int:
    undo = _args("undo")
    undo.db = str(db_path)
    return cmd_dedup(undo)


def _resolve(db_path: Path, quarantine: Path) -> int:
    args = _args("resolve", "--move-to", str(quarantine))
    args.db = str(db_path)
    return cmd_dedup(args)


def _unresolved(db_path: Path) -> list[dict]:
    db = ProgressDB(db_path=db_path)
    try:
        return db.list_dedup_groups()
    finally:
        db.close()


def test_an_interrupted_resolve_stays_listed_and_undoable(tmp_path, monkeypatch, capsys):
    from pyimgtag.commands import dedup as dedup_mod

    db_path, paths = _four_copies(tmp_path)
    quarantine = tmp_path / "q"
    real_move = dedup_mod._move_loser
    calls = {"n": 0}

    def _move_then_ctrl_c(path, root):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt
        return real_move(path, root)

    monkeypatch.setattr(dedup_mod, "_move_loser", _move_then_ctrl_c)
    with pytest.raises(KeyboardInterrupt):
        _resolve(db_path, quarantine)
    monkeypatch.setattr(dedup_mod, "_move_loser", real_move)
    assert sum(not Path(p).exists() for p in paths[1:]) == 1

    # Not marked resolved: the copies it never reached are still planned...
    (group,) = _unresolved(db_path)
    assert group["resolved_at"] is None
    # ...and undo still finds and restores the one that moved.
    assert _undo(db_path) == 0
    assert "Restored 1 file(s)" in capsys.readouterr().out
    assert all(Path(p).exists() for p in paths)


def test_a_rerun_finishes_an_interrupted_resolve(tmp_path, monkeypatch, capsys):
    from pyimgtag.commands import dedup as dedup_mod

    db_path, paths = _four_copies(tmp_path)
    quarantine = tmp_path / "q"
    real_move = dedup_mod._move_loser
    calls = {"n": 0}

    def _move_then_ctrl_c(path, root):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt
        return real_move(path, root)

    monkeypatch.setattr(dedup_mod, "_move_loser", _move_then_ctrl_c)
    with pytest.raises(KeyboardInterrupt):
        _resolve(db_path, quarantine)
    monkeypatch.setattr(dedup_mod, "_move_loser", real_move)
    capsys.readouterr()

    assert _resolve(db_path, quarantine) == 0
    out = capsys.readouterr().out
    assert "already move:" in out
    assert "move failed" not in out
    assert not any(Path(p).exists() for p in paths[1:])
    assert _unresolved(db_path) == []
    assert _undo(db_path) == 0
    assert all(Path(p).exists() for p in paths)


def test_an_interrupt_right_after_a_move_is_still_recorded(tmp_path, monkeypatch, capsys):
    """The move is recorded before it happens, so no file escapes undo."""
    from pyimgtag.commands import dedup as dedup_mod

    db_path, paths = _four_copies(tmp_path)
    real_move = dedup_mod._move_loser

    def _move_then_ctrl_c(path, root):
        real_move(path, root)
        raise KeyboardInterrupt  # after the file moved, before resolve continues

    monkeypatch.setattr(dedup_mod, "_move_loser", _move_then_ctrl_c)
    with pytest.raises(KeyboardInterrupt):
        _resolve(db_path, tmp_path / "q")
    assert _undo(db_path) == 0
    assert all(Path(p).exists() for p in paths)


def test_an_interrupted_undo_can_be_run_again(tmp_path, monkeypatch, capsys):
    from pyimgtag.commands import dedup as dedup_mod

    db_path, paths = _four_copies(tmp_path)
    assert _resolve(db_path, tmp_path / "q") == 0
    real_move = dedup_mod.safe_move
    calls = {"n": 0}

    def _restore_then_ctrl_c(src, dst, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt
        return real_move(src, dst, **kwargs)

    monkeypatch.setattr(dedup_mod, "safe_move", _restore_then_ctrl_c)
    with pytest.raises(KeyboardInterrupt):
        _undo(db_path)
    monkeypatch.setattr(dedup_mod, "safe_move", real_move)
    assert _undo(db_path) == 0
    assert all(Path(p).exists() for p in paths)
    (group,) = _unresolved(db_path)
    assert all(m["action"] is None for m in group["members"])


def test_a_rescan_keeps_the_records_of_a_group_acted_on(tmp_path, monkeypatch, capsys):
    from pyimgtag.commands import dedup as dedup_mod

    db_path, paths = _four_copies(tmp_path)
    real_move = dedup_mod._move_loser
    calls = {"n": 0}

    def _move_then_ctrl_c(path, root):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt
        return real_move(path, root)

    monkeypatch.setattr(dedup_mod, "_move_loser", _move_then_ctrl_c)
    with pytest.raises(KeyboardInterrupt):
        _resolve(db_path, tmp_path / "q")
    db = ProgressDB(db_path=db_path)
    db.replace_unresolved_dedup_groups([("duplicate", paths)], 5)  # what `dedup scan` does
    db.close()
    assert _undo(db_path) == 0
    assert all(Path(p).exists() for p in paths)


def test_a_keeper_moved_by_an_earlier_run_is_never_left_without_a_copy(tmp_path, capsys):
    db_path, paths = _four_copies(tmp_path)
    best = _unresolved(db_path)[0]
    from pyimgtag.dedup_groups import summarize_group

    keeper = summarize_group(best)["best_path"]
    # An earlier run (other --prefer) moved today's best pick away.
    target = tmp_path / "q" / Path(keeper).name
    target.parent.mkdir(parents=True)
    Path(keeper).rename(target)
    db = ProgressDB(db_path=db_path)
    db.record_dedup_action(best["id"], keeper, ACTION_MOVE, str(target))
    db.close()

    assert _resolve(db_path, tmp_path / "q2") == 0
    in_place = [p for p in paths if Path(p).exists()]
    assert len(in_place) == 1  # one copy always stays in the library


def test_undo_also_finds_a_partial_group_left_by_an_older_crash(tmp_path, capsys):
    """A DB where a move was recorded but the group never got resolved_at."""
    db_path, paths = _four_copies(tmp_path)
    quarantine = tmp_path / "q"
    target = quarantine / "a.jpg"
    target.parent.mkdir(parents=True)
    Path(paths[1]).rename(target)
    db = ProgressDB(db_path=db_path)
    group_id = db.list_dedup_groups()[0]["id"]
    db.record_dedup_action(group_id, paths[1], ACTION_MOVE, str(target))
    db.close()

    assert _undo(db_path) == 0
    assert "Restored 1 file(s)" in capsys.readouterr().out
    assert Path(paths[1]).exists()


def test_a_console_that_cannot_print_a_name_does_not_stop_the_resolve(
    tmp_path, monkeypatch, capsys
):
    import builtins

    db_path, paths = _four_copies(tmp_path)
    real_print = builtins.print

    def _cp1252_print(*a, **k):
        text = " ".join(str(x) for x in a)
        text.encode("cp1252")  # raises UnicodeEncodeError on the Cyrillic name
        real_print(*a, **k)

    monkeypatch.setattr(builtins, "print", _cp1252_print)
    args = _args("resolve", "--move-to", str(tmp_path / "q"))
    args.db = str(db_path)
    assert cmd_dedup(args) == 0
    assert not any(Path(p).exists() for p in paths[1:])
    monkeypatch.setattr(builtins, "print", real_print)
    assert _undo(db_path) == 0
    assert all(Path(p).exists() for p in paths)


def test_one_member_failing_to_move_still_resolves_the_others(tmp_path, monkeypatch, capsys):
    from pyimgtag.commands import dedup as dedup_mod

    db_path, paths = _four_copies(tmp_path)
    real_move = dedup_mod._move_loser

    def _locked_b(path, root):
        if path.endswith("b.jpg"):
            return None, "simulated EACCES"
        return real_move(path, root)

    monkeypatch.setattr(dedup_mod, "_move_loser", _locked_b)
    args = _args("resolve", "--move-to", str(tmp_path / "q"))
    args.db = str(db_path)
    assert cmd_dedup(args) == 1
    db = ProgressDB(db_path=db_path)
    group = db.list_dedup_groups(include_resolved=True)[0]
    db.close()
    assert group["resolved_at"] is not None
    assert _undo(db_path) == 0
    assert all(Path(p).exists() for p in paths)


def test_a_rerun_skips_members_an_earlier_run_already_moved(tmp_path, capsys):
    db_path, paths = _four_copies(tmp_path)
    quarantine = tmp_path / "q"
    target = quarantine / "a.jpg"
    target.parent.mkdir(parents=True)
    Path(paths[1]).rename(target)
    db = ProgressDB(db_path=db_path)
    group_id = db.list_dedup_groups()[0]["id"]
    db.record_dedup_action(group_id, paths[1], ACTION_MOVE, str(target))
    db.close()

    args = _args("resolve", "--move-to", str(quarantine))
    args.db = str(db_path)
    assert cmd_dedup(args) == 0
    out = capsys.readouterr().out
    assert f"already move: {paths[1]}" in out
    assert "move failed" not in out


# --- second review: partial cross-device copies, pinned keeper ----------------


def test_an_interrupted_cross_device_copy_never_replaces_the_original(tmp_path, monkeypatch):
    """A Ctrl-C mid-copy leaves a truncated file at the destination; neither a
    re-run nor undo may let it replace the intact original."""
    from pyimgtag import dedup_groups

    db_path, paths = _four_copies(tmp_path)
    originals = {p: Path(p).read_bytes() for p in paths}
    quarantine = tmp_path / "q"

    def _partial_copy_then_ctrl_c(src, dst, **kwargs):
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        Path(dst).write_bytes(b"trunc")  # what an interrupted copy2 leaves
        raise KeyboardInterrupt

    from pyimgtag.commands import dedup as dedup_mod

    monkeypatch.setattr(dedup_mod, "safe_move", _partial_copy_then_ctrl_c)
    with pytest.raises(KeyboardInterrupt):
        _resolve(db_path, quarantine)
    monkeypatch.setattr(dedup_mod, "safe_move", dedup_groups.safe_move)

    # Undo straight away: the original stays as it was.
    assert _undo(db_path) == 0
    assert {p: Path(p).read_bytes() for p in paths} == originals

    # And a re-run moves the copy properly, then undo restores it intact.
    assert _resolve(db_path, quarantine) == 0
    assert _undo(db_path) == 0
    assert {p: Path(p).read_bytes() for p in paths} == originals


def test_safe_move_across_devices_is_atomic_at_the_destination(tmp_path, monkeypatch):
    import errno as _errno

    from pyimgtag import dedup_groups

    src = tmp_path / "a.jpg"
    src.write_bytes(b"x" * 100)
    dst = tmp_path / "q" / "a.jpg"

    def _cross_device(a, b):
        raise OSError(_errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(dedup_groups.os, "rename", _cross_device)

    def _copy_then_ctrl_c(a, b):
        Path(b).write_bytes(b"part")
        raise KeyboardInterrupt

    monkeypatch.setattr(dedup_groups.shutil, "copy2", _copy_then_ctrl_c)
    with pytest.raises(KeyboardInterrupt):
        dedup_groups.safe_move(src, dst)
    assert src.read_bytes() == b"x" * 100
    assert not dst.exists()  # only a temporary name was ever partial
    assert list(dst.parent.iterdir()) == []


def test_the_keeper_is_pinned_from_the_first_action(tmp_path, monkeypatch):
    """A re-run with another ranking keeps the copy the first run kept."""
    from pyimgtag.commands import dedup as dedup_mod

    db_path, paths = _four_copies(tmp_path)
    real_move = dedup_mod._move_loser
    calls = {"n": 0}

    def _move_then_ctrl_c(path, root):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt
        return real_move(path, root)

    monkeypatch.setattr(dedup_mod, "_move_loser", _move_then_ctrl_c)
    with pytest.raises(KeyboardInterrupt):
        _resolve(db_path, tmp_path / "q")
    monkeypatch.setattr(dedup_mod, "_move_loser", real_move)
    (group,) = _unresolved(db_path)
    first_keeper = group["keep_path"]
    assert first_keeper is not None

    args = _args("resolve", "--move-to", str(tmp_path / "q"), "--prefer", "mtime")
    args.db = str(db_path)
    assert cmd_dedup(args) == 0
    assert Path(first_keeper).exists()
    assert [p for p in paths if Path(p).exists()] == [first_keeper]


def test_an_unplugged_quarantine_keeps_the_records_for_a_later_undo(tmp_path, capsys):
    """Undo with the quarantine drive away must not forget what was moved."""
    db_path, paths = _four_copies(tmp_path)
    quarantine = tmp_path / "drive" / "q"
    assert _resolve(db_path, quarantine) == 0
    away = tmp_path / "drive-unplugged"
    (tmp_path / "drive").rename(away)

    assert _undo(db_path) == 1
    assert "quarantine not reachable" in capsys.readouterr().err
    away.rename(tmp_path / "drive")

    assert _undo(db_path) == 0
    assert all(Path(p).exists() for p in paths)


def test_safe_move_never_overwrites_an_existing_destination(tmp_path):
    from pyimgtag.dedup_groups import safe_move

    src = tmp_path / "a.jpg"
    src.write_bytes(b"new")
    dst = tmp_path / "q" / "a.jpg"
    dst.parent.mkdir()
    dst.write_bytes(b"old")
    with pytest.raises(FileExistsError):
        safe_move(src, dst)
    assert (src.read_bytes(), dst.read_bytes()) == (b"new", b"old")


def test_a_leftover_in_the_quarantine_gets_a_new_name_not_overwritten(tmp_path):
    from pyimgtag.dedup_groups import free_destination

    dest = tmp_path / "a.jpg"
    assert free_destination(dest) == dest
    dest.write_bytes(b"x")
    (tmp_path / "a~1.jpg").write_bytes(b"x")
    assert free_destination(dest) == tmp_path / "a~2.jpg"


def test_a_run_that_changed_nothing_drops_the_keeper_pin(tmp_path, monkeypatch):
    from pyimgtag.commands import dedup as dedup_mod

    db_path, _ = _four_copies(tmp_path)
    monkeypatch.setattr(dedup_mod, "_move_loser", lambda path, root: (None, "read-only"))
    assert _resolve(db_path, tmp_path / "q") == 1
    (group,) = _unresolved(db_path)
    assert group["keep_path"] is None
    assert all(m["action"] is None for m in group["members"])


def test_undo_never_recreates_a_library_folder_that_is_not_there(tmp_path, capsys):
    """An unplugged library drive: restoring would land on the wrong disk."""
    db_path, paths = _four_copies(tmp_path)
    assert _resolve(db_path, tmp_path / "q") == 0
    (tmp_path / "lib").rename(tmp_path / "lib-unplugged")

    assert _undo(db_path) == 1
    assert "original location not reachable" in capsys.readouterr().err
    assert not (tmp_path / "lib").exists()

    (tmp_path / "lib-unplugged").rename(tmp_path / "lib")
    assert _undo(db_path) == 0
    assert all(Path(p).exists() for p in paths)


@pytest.mark.skipif(
    sys.platform == "win32", reason="Windows needs a writable handle; there it is best effort"
)
def test_fsync_flushes_a_read_only_copy(tmp_path, monkeypatch):
    import os as _os

    from pyimgtag import dedup_groups

    f = tmp_path / "ro.jpg"
    f.write_bytes(b"x")
    f.chmod(0o444)
    calls = []
    real = _os.fsync
    monkeypatch.setattr(dedup_groups.os, "fsync", lambda fd: (calls.append(fd), real(fd)))
    dedup_groups._fsync_file(f)
    assert calls
