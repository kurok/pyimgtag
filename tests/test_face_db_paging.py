"""Faces and persons are paged, counted and looked up in SQL (#400).

Every paged query must equal slicing the full list it replaces, in the same
order, and the batch assignment must be all-or-nothing.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pyimgtag.progress_db import ProgressDB

_PAGES = [(None, 0), (1, 0), (2, 1), (3, 2), (5, 0), (2, 7), (10, 100), (None, 3), (0, 0)]


def _seed(path: Path) -> ProgressDB:
    db = ProgressDB(path)
    conn = db._conn
    conn.executemany(
        "INSERT INTO persons (id, label, confirmed, source, trusted) VALUES (?, ?, ?, ?, ?)",
        [
            (1, "Ann", 1, "auto", 1),
            (2, "", 0, "auto", 0),
            (3, "Bo", 0, "photos", 0),
            (4, "", 0, "auto", 0),
        ],
    )
    # (person_id, ignored, confidence): ties, zero confidence, unassigned, trash.
    faces = [
        (1, 0, 0.9),
        (1, 0, 0.0),
        (1, 0, 0.5),
        (1, 0, 0.9),
        (1, 0, 0.1),
        (2, 0, 0.7),
        (2, 0, 0.7),
        (2, 0, 0.8),
        (3, 0, 0.3),
        (3, 0, 0.6),
        (3, 0, 0.2),
        (3, 0, 0.9),
        (None, 0, 0.4),
        (None, 0, 0.0),
        (None, 0, 0.95),
        (None, 0, 0.4),
        (None, 1, 0.5),
        (None, 1, 0.6),
        (2, 1, 0.1),
    ]
    conn.executemany(
        "INSERT INTO faces (image_path, person_id, ignored, confidence) VALUES (?, ?, ?, ?)",
        [(f"/p/{i}.jpg", pid, ign, conf) for i, (pid, ign, conf) in enumerate(faces)],
    )
    conn.commit()
    return db


@pytest.fixture
def db(tmp_path: Path):
    database = _seed(tmp_path / "p.db")
    yield database
    database.close()


def _slice(rows: list[dict], limit: int | None, offset: int) -> list[dict]:
    end = None if limit is None else offset + limit
    return rows[offset:end]


def _by_confidence(rows: list[dict]) -> list[dict]:
    # What the routes did in Python: stable sort, missing confidence as 0.
    return sorted(rows, key=lambda f: f.get("confidence") or 0.0, reverse=True)


@pytest.mark.parametrize(("limit", "offset"), _PAGES)
def test_unassigned_pages_equal_slicing_the_full_list(db, limit, offset):
    full = db.get_unassigned_faces()
    assert db.get_unassigned_faces(limit, offset) == _slice(full, limit, offset)
    by_conf = _by_confidence(full)
    assert db.get_unassigned_faces(limit, offset, order_by_confidence=True) == _slice(
        by_conf, limit, offset
    )
    assert db.count_unassigned_faces() == len(full) == 4


@pytest.mark.parametrize(("limit", "offset"), _PAGES)
def test_ignored_pages_equal_slicing_the_full_list(db, limit, offset):
    full = db.get_ignored_faces()
    assert db.get_ignored_faces(limit, offset) == _slice(full, limit, offset)
    assert db.count_ignored_faces() == len(full) == 3


@pytest.mark.parametrize("person_id", [1, 2, 3, 4, 99])
@pytest.mark.parametrize(("limit", "offset"), _PAGES)
def test_person_pages_equal_sorting_then_slicing(db, person_id, limit, offset):
    full = db.get_faces_for_person(person_id)
    assert db.get_faces_for_person(person_id, limit, offset) == _slice(full, limit, offset)
    assert db.get_faces_for_person(person_id, limit, offset, order_by_confidence=True) == _slice(
        _by_confidence(full), limit, offset
    )
    assert db.count_faces_for_person(person_id) == len(full)


def test_no_argument_calls_still_return_every_row(db):
    assert [f["id"] for f in db.get_unassigned_faces()] == [13, 14, 15, 16]
    assert len(db.get_faces_for_person(1)) == 5
    assert {"id", "image_path", "bbox_x", "bbox_y", "bbox_w", "bbox_h", "confidence"} == set(
        db.get_ignored_faces()[0]
    )


def test_get_person_matches_get_persons(db):
    everyone = {p.person_id: p for p in db.get_persons()}
    for pid, person in everyone.items():
        assert db.get_person(pid) == person
        assert db.person_exists(pid)
    assert db.get_person(99) is None
    assert not db.person_exists(99)


def test_largest_other_person_matches_max_over_get_persons(db):
    persons = db.get_persons()
    for pid in (1, 2, 3, 4, 99):
        others = [p for p in persons if p.person_id != pid and p.face_ids]
        expected = max(others, key=lambda p: len(p.face_ids), default=None)
        got = db.largest_other_person(pid)
        if expected is None:
            assert got is None
        else:
            assert got == (expected.person_id, expected.label)


def test_largest_other_person_breaks_ties_by_lowest_id(tmp_path):
    database = ProgressDB(tmp_path / "t.db")
    conn = database._conn
    conn.executemany(
        "INSERT INTO persons (id, label) VALUES (?, ?)", [(5, "e"), (6, "f"), (7, "g")]
    )
    conn.executemany(
        "INSERT INTO faces (image_path, person_id) VALUES (?, ?)",
        [("/a", 6), ("/b", 6), ("/c", 5), ("/d", 5)],
    )
    conn.commit()
    assert database.largest_other_person(7) == (5, "e")
    database.close()


def test_set_person_id_many_assigns_every_face(db):
    db.set_person_id_many([13, 14, 15], 3)
    assert [f["id"] for f in db.get_unassigned_faces()] == [16]
    assert db.count_faces_for_person(3) == 7


def test_set_person_id_many_is_atomic(db):
    # A real failure partway through: the third update hits a trigger.
    db._conn.execute(
        "CREATE TRIGGER fail_on_15 BEFORE UPDATE ON faces WHEN NEW.id = 15 "
        "BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
    )
    db._conn.commit()
    before = db._conn.execute("SELECT id, person_id FROM faces ORDER BY id").fetchall()

    with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
        db.set_person_id_many([13, 14, 15, 16], 2)

    after = db._conn.execute("SELECT id, person_id FROM faces ORDER BY id").fetchall()
    assert after == before


def test_new_methods_run_under_the_connection_lock():
    for name in (
        "get_person",
        "person_exists",
        "largest_other_person",
        "count_unassigned_faces",
        "count_ignored_faces",
        "count_faces_for_person",
        "set_person_id_many",
    ):
        assert getattr(getattr(ProgressDB, name), "__pyimgtag_locked__", False), name


@pytest.mark.parametrize("offset", [2**63 - 1, 2**63, 10**20])
def test_a_huge_offset_is_an_empty_page_not_an_error(db, offset):
    assert db.get_unassigned_faces(5, offset) == []
    assert db.get_ignored_faces(None, offset) == []
    assert db.get_faces_for_person(1, 5, offset, order_by_confidence=True) == []
