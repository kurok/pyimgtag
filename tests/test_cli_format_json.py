"""``--format json`` for status, cleanup, tags list and faces review (#404).

JSON goes to stdout and nothing else does; the default ``table`` output is
unchanged byte for byte.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pyimgtag.main import main
from pyimgtag.models import FaceDetection, ImageResult
from pyimgtag.progress_db import ProgressDB


@pytest.fixture(autouse=True)
def _no_update_check(monkeypatch):
    """The PyPI update banner goes to stderr; keep it (and the network) out."""
    monkeypatch.setenv("PYIMGTAG_NO_UPDATE_CHECK", "1")


def _seeded(tmp_path: Path) -> Path:
    path = tmp_path / "p.db"
    db = ProgressDB(db_path=path)
    for name, tags, cleanup in (
        ("a.jpg", ["beach", "sea"], "delete"),
        ("b.jpg", ["beach"], "review"),
        ("c.jpg", [], None),
    ):
        img = tmp_path / name
        img.write_bytes(b"\x00" * 10)
        db.mark_done(
            img,
            ImageResult(
                file_path=str(img),
                file_name=name,
                tags=tags,
                cleanup_class=cleanup,
                image_date="2024-05-01T10:00:00" if name == "a.jpg" else None,
                nearest_city="Nice" if name == "a.jpg" else None,
                nearest_country="France" if name == "a.jpg" else None,
            ),
        )
    bad = tmp_path / "d.jpg"
    bad.write_bytes(b"\x00")
    db.mark_done(
        bad,
        ImageResult(
            file_path=str(bad), file_name="d.jpg", processing_status="error", error_message="x"
        ),
    )
    ann = db.create_person("Ann", confirmed=True)
    unnamed = db.create_person()
    box = FaceDetection(image_path=str(tmp_path / "a.jpg"), bbox_x=0, bbox_y=0, bbox_w=1, bbox_h=1)
    for pid in (ann, ann, unnamed, None):
        face = db.insert_face(str(tmp_path / "a.jpg"), box)
        if pid is not None:
            db.set_person_id(face, pid)
    db.close()
    return path


def _empty(tmp_path: Path) -> Path:
    path = tmp_path / "empty.db"
    ProgressDB(db_path=path).close()
    return path


def _json(capsys, *argv: str):
    assert main(list(argv)) == 0
    out = capsys.readouterr().out
    return json.loads(out)


class TestStatus:
    def test_seeded(self, tmp_path, capsys):
        db = str(_seeded(tmp_path))
        assert _json(capsys, "status", "--db", db, "--format", "json") == {
            "total": 4,
            "ok": 3,
            "error": 1,
            "pending": 0,
        }

    def test_empty(self, tmp_path, capsys):
        db = str(_empty(tmp_path))
        assert _json(capsys, "status", "--db", db, "--format", "json") == {
            "total": 0,
            "ok": 0,
            "error": 0,
            "pending": 0,
        }

    def test_table_is_unchanged(self, tmp_path, capsys):
        db = str(_seeded(tmp_path))
        assert main(["status", "--db", db]) == 0
        out = capsys.readouterr().out
        assert out == "Progress: 3 / 4 (75%)\n  ok:      3\n  error:   1\n  pending: 0\n"


class TestTagsList:
    def test_seeded(self, tmp_path, capsys):
        db = str(_seeded(tmp_path))
        assert _json(capsys, "tags", "list", "--db", db, "--format", "json") == [
            {"tag": "beach", "count": 2},
            {"tag": "sea", "count": 1},
        ]

    def test_empty_is_an_empty_list_on_stdout(self, tmp_path, capsys):
        db = str(_empty(tmp_path))
        assert main(["tags", "list", "--db", db, "--format", "json"]) == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out) == []
        assert captured.err == ""

    def test_table_is_unchanged(self, tmp_path, capsys):
        db = str(_seeded(tmp_path))
        assert main(["tags", "list", "--db", db]) == 0
        assert capsys.readouterr().out == (
            "TAG    COUNT\n-------------\nbeach  2\nsea    1\n\n2 unique tag(s).\n"
        )

    def test_empty_table_is_unchanged(self, tmp_path, capsys):
        db = str(_empty(tmp_path))
        assert main(["tags", "list", "--db", db]) == 0
        captured = capsys.readouterr()
        assert (captured.out, captured.err) == ("", "No tags found.\n")


class TestCleanup:
    def test_seeded(self, tmp_path, capsys):
        db = str(_seeded(tmp_path))
        doc = _json(capsys, "cleanup", "--db", db, "--format", "json")
        assert doc == {
            "candidates": [
                {
                    "file_path": str(tmp_path / "a.jpg"),
                    "cleanup_class": "delete",
                    "tags": ["beach", "sea"],
                    "image_date": "2024-05-01T10:00:00",
                    "nearest_city": "Nice",
                    "nearest_country": "France",
                }
            ],
            "duplicate_groups": 0,
            "reclaimable_bytes": 0,
        }

    def test_include_review(self, tmp_path, capsys):
        db = str(_seeded(tmp_path))
        doc = _json(capsys, "cleanup", "--db", db, "--include-review", "--format", "json")
        assert [c["file_path"] for c in doc["candidates"]] == [
            str(tmp_path / "a.jpg"),
            str(tmp_path / "b.jpg"),
        ]
        assert doc["candidates"][1]["cleanup_class"] == "review"

    def test_empty(self, tmp_path, capsys):
        db = str(_empty(tmp_path))
        assert main(["cleanup", "--db", db, "--format", "json"]) == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out) == {
            "candidates": [],
            "duplicate_groups": 0,
            "reclaimable_bytes": 0,
        }
        assert captured.err == ""

    def test_duplicate_groups_are_in_the_json_not_on_stdout_as_text(self, tmp_path, capsys):
        path = _seeded(tmp_path)
        with ProgressDB(db_path=path) as db:
            db.replace_unresolved_dedup_groups(
                [("exact", [str(tmp_path / "a.jpg"), str(tmp_path / "b.jpg")])], 4
            )
        captured_json = _json(capsys, "cleanup", "--db", str(path), "--format", "json")
        assert captured_json["duplicate_groups"] == 1
        assert captured_json["reclaimable_bytes"] == 10

        assert main(["cleanup", "--db", str(path)]) == 0
        assert capsys.readouterr().out.startswith(
            "Duplicate groups: 1 (10 B reclaimable) — see 'pyimgtag dedup list'\n"
            "Cleanup candidates (delete): 1\n"
        )

    def test_table_is_unchanged(self, tmp_path, capsys):
        db = str(_seeded(tmp_path))
        assert main(["cleanup", "--db", db]) == 0
        a = tmp_path / "a.jpg"
        assert capsys.readouterr().out == (
            "Cleanup candidates (delete): 1\n\n"
            f"  [delete]  {a}  | Nice, France  | 2024-05-01  | tags: beach, sea\n"
        )


class TestFacesReview:
    def test_seeded(self, tmp_path, capsys):
        db = str(_seeded(tmp_path))
        doc = _json(capsys, "faces", "review", "--db", db, "--format", "json")
        assert doc == [
            {"person_id": 1, "label": "Ann", "confirmed": True, "face_count": 2},
            {"person_id": 2, "label": "", "confirmed": False, "face_count": 1},
        ]

    def test_empty(self, tmp_path, capsys):
        db = str(_empty(tmp_path))
        assert main(["faces", "review", "--db", db, "--format", "json"]) == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out) == []
        assert captured.err == ""

    def test_table_is_unchanged(self, tmp_path, capsys):
        db = str(_seeded(tmp_path))
        assert main(["faces", "review", "--db", db]) == 0
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == (
            "Faces: 4 total, 3 assigned, 1 unassigned\n"
            "\nPersons (2):\n"
            "  [confirmed] Ann: 2 face(s)\n"
            "  [auto] (unlabelled #2): 1 face(s)\n"
            "\n1 face(s) not assigned to any person.\n"
            "Run 'pyimgtag faces cluster' to group them.\n"
        )


def test_non_ascii_tags_and_labels_round_trip(tmp_path, capsys):
    path = tmp_path / "u.db"
    with ProgressDB(db_path=path) as db:
        img = tmp_path / "u.jpg"
        img.write_bytes(b"\x00")
        db.mark_done(img, ImageResult(file_path=str(img), file_name="u.jpg", tags=["café", "日本"]))
        db.create_person("Zoë")
    tags = _json(capsys, "tags", "list", "--db", str(path), "--format", "json")
    assert {t["tag"] for t in tags} == {"café", "日本"}
    people = _json(capsys, "faces", "review", "--db", str(path), "--format", "json")
    assert people[0]["label"] == "Zoë"


@pytest.mark.parametrize(
    "argv",
    [["status"], ["cleanup"], ["tags", "list"], ["faces", "review"]],
    ids=["status", "cleanup", "tags-list", "faces-review"],
)
def test_a_missing_database_is_still_refused_with_json(tmp_path, capsys, argv):
    missing = tmp_path / "nope.db"
    assert main([*argv, "--db", str(missing), "--format", "json"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no database at" in captured.err
