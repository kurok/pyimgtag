"""Tests for the dedup schema, DedupDB, and its ProgressDB delegates."""

from __future__ import annotations

from pathlib import Path

from pyimgtag.db.dedup_db import ACTION_MOVE, ACTION_TAG
from pyimgtag.models import ImageResult
from pyimgtag.progress_db import ProgressDB

HASH_A = "ffffffffffffffff"
HASH_A_NEAR = "fffffffffffffff0"  # 4 bits from HASH_A


def _seed(db: ProgressDB, tmp_path: Path, name: str, phash: str | None, size: int = 100) -> str:
    path = tmp_path / name
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


def _open(tmp_path: Path) -> ProgressDB:
    return ProgressDB(db_path=tmp_path / "progress.db")


# --- schema ----------------------------------------------------------------


def test_migration_is_idempotent_across_reopens(tmp_path):
    db = _open(tmp_path)
    version = db._conn.execute("PRAGMA user_version").fetchone()[0]
    db.close()
    again = _open(tmp_path)
    assert again._conn.execute("PRAGMA user_version").fetchone()[0] == version
    cols = {r[1] for r in again._conn.execute("PRAGMA table_info(processed_images)")}
    assert {"phash", "width", "height"} <= cols
    tables = {
        r[0] for r in again._conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {"dedup_groups", "dedup_members"} <= tables
    again.close()


def test_mark_done_persists_the_phash(tmp_path):
    db = _open(tmp_path)
    path = _seed(db, tmp_path, "a.jpg", HASH_A)
    assert db.all_phashes() == [(path, HASH_A)]
    db.close()


# --- phash bookkeeping -----------------------------------------------------


def test_iter_paths_missing_phash_skips_hashed_rows(tmp_path):
    db = _open(tmp_path)
    hashed = _seed(db, tmp_path, "a.jpg", HASH_A)
    unhashed = _seed(db, tmp_path, "b.jpg", None)
    # ``hashed`` still has no width/height, so it is returned too.
    assert set(db.iter_paths_missing_phash()) == {hashed, unhashed}
    db.set_phash(hashed, HASH_A, 100, 50)
    assert list(db.iter_paths_missing_phash()) == [unhashed]
    assert set(db.iter_paths_missing_phash(include_hashed=True)) == {hashed, unhashed}
    db.close()


def test_set_phash_stores_dimensions(tmp_path):
    db = _open(tmp_path)
    path = _seed(db, tmp_path, "a.jpg", None)
    db.set_phash(path, HASH_A, 640, 480)
    row = db._conn.execute(
        "SELECT phash, width, height FROM processed_images WHERE file_path = ?", (path,)
    ).fetchone()
    assert row == (HASH_A, 640, 480)
    db.close()


# --- group bookkeeping -----------------------------------------------------


def test_replace_unresolved_groups_round_trip(tmp_path):
    db = _open(tmp_path)
    a = _seed(db, tmp_path, "a.jpg", HASH_A, size=100)
    b = _seed(db, tmp_path, "b.jpg", HASH_A_NEAR, size=300)
    assert db.replace_unresolved_dedup_groups([("duplicate", [a, b])], 5) == 1
    groups = db.list_dedup_groups()
    assert len(groups) == 1
    assert groups[0]["kind"] == "duplicate"
    assert groups[0]["threshold"] == 5
    assert [m["file_path"] for m in groups[0]["members"]] == sorted([a, b])
    assert {m["file_size"] for m in groups[0]["members"]} == {100, 300}
    db.close()


def test_rescan_keeps_group_id_and_adds_new_members(tmp_path):
    db = _open(tmp_path)
    a = _seed(db, tmp_path, "a.jpg", HASH_A)
    b = _seed(db, tmp_path, "b.jpg", HASH_A_NEAR)
    db.replace_unresolved_dedup_groups([("duplicate", [a, b])], 5)
    first_id = db.list_dedup_groups()[0]["id"]

    c = _seed(db, tmp_path, "c.jpg", HASH_A)
    db.replace_unresolved_dedup_groups([("duplicate", [a, b, c])], 5)
    groups = db.list_dedup_groups()
    assert len(groups) == 1
    assert groups[0]["id"] == first_id
    assert [m["file_path"] for m in groups[0]["members"]] == sorted([a, b, c])
    db.close()


def test_resolved_groups_survive_a_rescan_untouched(tmp_path):
    db = _open(tmp_path)
    a = _seed(db, tmp_path, "a.jpg", HASH_A)
    b = _seed(db, tmp_path, "b.jpg", HASH_A_NEAR)
    db.replace_unresolved_dedup_groups([("duplicate", [a, b])], 5)
    group_id = db.list_dedup_groups()[0]["id"]
    db.record_dedup_action(group_id, b, ACTION_MOVE, "/quarantine/b.jpg")
    db.mark_dedup_resolved(group_id, a)

    # A fresh scan would still put a and b together, plus a new photo.
    c = _seed(db, tmp_path, "c.jpg", HASH_A)
    d = _seed(db, tmp_path, "d.jpg", HASH_A_NEAR)
    db.replace_unresolved_dedup_groups([("duplicate", [a, b, c, d])], 5)

    resolved = db.get_dedup_group(group_id)
    assert resolved["resolved_at"] is not None
    assert resolved["keep_path"] == a
    assert [m["file_path"] for m in resolved["members"]] == sorted([a, b])
    # c and d form a new unresolved group; a and b are locked out of it.
    unresolved = db.list_dedup_groups()
    assert len(unresolved) == 1
    assert [m["file_path"] for m in unresolved[0]["members"]] == sorted([c, d])
    db.close()


def test_stale_unresolved_groups_are_dropped(tmp_path):
    db = _open(tmp_path)
    a = _seed(db, tmp_path, "a.jpg", HASH_A)
    b = _seed(db, tmp_path, "b.jpg", HASH_A_NEAR)
    db.replace_unresolved_dedup_groups([("duplicate", [a, b])], 5)
    assert len(db.list_dedup_groups()) == 1
    db.replace_unresolved_dedup_groups([], 5)
    assert db.list_dedup_groups() == []
    assert db._conn.execute("SELECT COUNT(*) FROM dedup_members").fetchone()[0] == 0
    db.close()


def test_singleton_groups_are_never_stored(tmp_path):
    db = _open(tmp_path)
    a = _seed(db, tmp_path, "a.jpg", HASH_A)
    assert db.replace_unresolved_dedup_groups([("duplicate", [a])], 5) == 0
    assert db.list_dedup_groups() == []
    db.close()


def test_list_groups_joins_the_judge_score(tmp_path):
    from pyimgtag.models import JudgeResult, JudgeScores

    db = _open(tmp_path)
    a = _seed(db, tmp_path, "a.jpg", HASH_A)
    b = _seed(db, tmp_path, "b.jpg", HASH_A_NEAR)
    db.save_judge_result(
        JudgeResult(
            file_path=a,
            file_name="a.jpg",
            weighted_score=8,
            core_score=8,
            visible_score=8,
            scores=JudgeScores(score=8, reason="r"),
        )
    )
    db.replace_unresolved_dedup_groups([("duplicate", [a, b])], 5)
    scores = {m["file_path"]: m["judge_score"] for m in db.list_dedup_groups()[0]["members"]}
    assert scores[a] == 8.0
    assert scores[b] is None
    db.close()


def test_record_resolve_and_undo(tmp_path):
    db = _open(tmp_path)
    a = _seed(db, tmp_path, "a.jpg", HASH_A)
    b = _seed(db, tmp_path, "b.jpg", HASH_A_NEAR)
    db.replace_unresolved_dedup_groups([("duplicate", [a, b])], 5)
    group_id = db.list_dedup_groups()[0]["id"]

    db.record_dedup_action(group_id, b, ACTION_MOVE, "/q/b.jpg")
    db.mark_dedup_resolved(group_id, a)
    group = db.get_dedup_group(group_id)
    moved = next(m for m in group["members"] if m["file_path"] == b)
    assert moved["action"] == ACTION_MOVE
    assert moved["moved_to"] == "/q/b.jpg"
    assert moved["acted_at"]

    assert db.undo_dedup_group(group_id) == 2
    group = db.get_dedup_group(group_id)
    assert group["resolved_at"] is None
    assert group["keep_path"] is None
    assert all(m["action"] is None and m["moved_to"] is None for m in group["members"])
    db.close()


def test_get_dedup_group_returns_none_for_unknown_id(tmp_path):
    db = _open(tmp_path)
    assert db.get_dedup_group(999) is None
    db.close()


def test_include_resolved_filters_the_listing(tmp_path):
    db = _open(tmp_path)
    a = _seed(db, tmp_path, "a.jpg", HASH_A)
    b = _seed(db, tmp_path, "b.jpg", HASH_A_NEAR)
    db.replace_unresolved_dedup_groups([("duplicate", [a, b])], 5)
    group_id = db.list_dedup_groups()[0]["id"]
    db.record_dedup_action(group_id, b, ACTION_TAG)
    db.mark_dedup_resolved(group_id, a)
    assert db.list_dedup_groups() == []
    assert len(db.list_dedup_groups(include_resolved=True)) == 1
    db.close()


# --- aggregates ------------------------------------------------------------


def test_totals_exclude_the_largest_copy_and_resolved_groups(tmp_path):
    db = _open(tmp_path)
    a = _seed(db, tmp_path, "a.jpg", HASH_A, size=100)
    b = _seed(db, tmp_path, "b.jpg", HASH_A_NEAR, size=300)
    db.replace_unresolved_dedup_groups([("duplicate", [a, b])], 5)
    assert db.get_dedup_totals() == {"groups": 1, "reclaimable_bytes": 100}
    db.mark_dedup_resolved(db.list_dedup_groups()[0]["id"], b)
    assert db.get_dedup_totals() == {"groups": 0, "reclaimable_bytes": 0}
    db.close()


def test_insights_housekeeping_reports_duplicate_totals(tmp_path):
    db = _open(tmp_path)
    a = _seed(db, tmp_path, "a.jpg", HASH_A, size=100)
    b = _seed(db, tmp_path, "b.jpg", HASH_A_NEAR, size=300)
    hk = db.get_insights()["housekeeping"]
    assert hk["duplicate_groups"] == 0
    assert hk["duplicates_reclaimable_bytes"] == 0

    db.replace_unresolved_dedup_groups([("duplicate", [a, b])], 5)
    hk = db.get_insights()["housekeeping"]
    assert hk["duplicate_groups"] == 1
    assert hk["duplicates_reclaimable_bytes"] == 100
    db.close()


# --- report rendering ------------------------------------------------------


def _doc_with_duplicates(groups: int, reclaimable: int) -> dict:
    return {
        "schema_version": 1,
        "empty": False,
        "overview": {
            "total": 2,
            "ok": 2,
            "error": 0,
            "by_status": {"ok": 2},
            "size_bytes": 400,
            "oldest": None,
            "newest": None,
        },
        "housekeeping": {
            "delete_candidates": {"count": 0, "bytes": 0},
            "review_candidates": {"count": 0, "bytes": 0},
            "untagged": 0,
            "errors": 0,
            "duplicate_groups": groups,
            "duplicates_reclaimable_bytes": reclaimable,
        },
    }


def test_terminal_report_shows_duplicate_totals():
    from pyimgtag.insights_report import render_terminal

    text = render_terminal(_doc_with_duplicates(3, 2048))
    assert "Duplicate groups" in text
    assert "2.0 KB reclaimable" in text


def test_terminal_report_hides_duplicates_when_there_are_none():
    from pyimgtag.insights_report import render_terminal

    assert "Duplicate groups" not in render_terminal(_doc_with_duplicates(0, 0))


def test_html_report_shows_duplicate_totals():
    from pyimgtag.insights_report import render_html

    html = render_html(_doc_with_duplicates(3, 2048))
    assert "duplicate groups" in html
    assert "duplicate groups" not in render_html(_doc_with_duplicates(0, 0))
