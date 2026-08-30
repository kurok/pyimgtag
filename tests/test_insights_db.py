"""Golden-number tests for :class:`pyimgtag.db.insights_db.InsightsDB`."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from pyimgtag.db.insights_db import INSIGHTS_SCHEMA_VERSION, MAX_TOP_PHOTOS
from pyimgtag.models import ImageResult, JudgeResult, JudgeScores
from pyimgtag.progress_db import ProgressDB


def _img(path: str, **kw) -> ImageResult:
    defaults = dict(
        file_path=path,
        file_name=path.rsplit("/", 1)[-1],
        source_type="directory",
        tags=["beach", "sunset"],
        scene_summary="s",
        processing_status="ok",
    )
    defaults.update(kw)
    return ImageResult(**defaults)


@pytest.fixture
def fixture_db(tmp_path):
    """A DB with known contents so every section has a golden value.

    6 rows total: 5 ok + 1 error. Two files exist on disk so file_size is
    non-zero for them (1000 and 2000 bytes).
    """
    db = ProgressDB(db_path=tmp_path / "progress.db")
    big = tmp_path / "big.jpg"
    big.write_bytes(b"x" * 2000)
    small = tmp_path / "small.jpg"
    small.write_bytes(b"y" * 1000)

    db.mark_done(
        big,
        _img(
            str(big),
            tags=["beach", "sunset", "Family"],
            image_date="2024-07-04T12:00:00",
            nearest_country="Portugal",
            nearest_region="Leiria",
            nearest_city="Óbidos",
            scene_category="outdoor_travel",
            emotional_tone="joyful",
            event_hint="vacation",
            has_text=False,
            cleanup_class="keep",
        ),
    )
    db.mark_done(
        small,
        _img(
            str(small),
            tags=["beach"],
            image_date="2024-07-04T18:00:00",
            nearest_country="Portugal",
            nearest_city="Lisbon",
            scene_category="outdoor_travel",
            emotional_tone="calm",
            has_text=True,
            cleanup_class="delete",
        ),
    )
    # Use the same OS-normalized string everywhere a given path is referenced
    # (mark_done stores str(Path(...)), which uses native separators on
    # Windows) so faces/judge rows join against processed_images correctly.
    c_path = str(Path("/lib/c.jpg"))
    d_path = str(Path("/lib/d.jpg"))
    e_path = str(Path("/lib/e.jpg"))
    err_path = str(Path("/lib/err.jpg"))
    db.mark_done(
        Path(c_path),
        _img(
            c_path,
            tags=["family", "indoor"],
            image_date="2023-01-15T09:00:00",
            nearest_country="Spain",
            nearest_city="Madrid",
            scene_category="people",
            emotional_tone="joyful",
            event_hint="birthday",
            cleanup_class="review",
        ),
    )
    db.mark_done(
        Path(d_path),
        _img(d_path, tags=[], image_date=None, scene_category="screenshot"),
    )
    db.mark_done(Path(e_path), _img(e_path, tags=["sunset"], image_date="2024-08-01"))
    db.mark_done(
        Path(err_path),
        _img(err_path, tags=[], processing_status="error", error_message="boom"),
    )

    for path, score in ((str(big), 9), (str(small), 4), (c_path, 7)):
        db.save_judge_result(
            JudgeResult(
                file_path=path,
                file_name=Path(path).name,
                weighted_score=score,
                core_score=score,
                visible_score=score,
                scores=JudgeScores(score=score, verdict="v", reason=f"because {score}"),
            )
        )

    alice = db.create_person("Alice", confirmed=True)
    bob = db.create_person("Bob")
    unnamed = db.create_person("")
    conn = db._conn
    rows = [
        (str(big), alice),
        (str(small), alice),
        (c_path, alice),
        (c_path, bob),
        (d_path, unnamed),
    ]
    conn.executemany("INSERT INTO faces (image_path, person_id, ignored) VALUES (?, ?, 0)", rows)
    # An ignored face must not count towards Bob.
    conn.execute(
        "INSERT INTO faces (image_path, person_id, ignored) VALUES (?, ?, 1)", (e_path, bob)
    )
    conn.commit()
    yield db
    db.close()


def test_schema_version_and_all_sections_present(fixture_db):
    doc = fixture_db.get_insights()
    assert doc["schema_version"] == INSIGHTS_SCHEMA_VERSION
    assert doc["empty"] is False
    assert set(doc) == {
        "schema_version",
        "empty",
        "overview",
        "time",
        "places",
        "content",
        "people",
        "quality",
        "housekeeping",
    }
    json.dumps(doc)  # must be serialisable


def test_overview_golden(fixture_db):
    ov = fixture_db.get_insights()["overview"]
    assert ov["total"] == 6
    assert ov["ok"] == 5
    assert ov["error"] == 1
    assert ov["by_status"] == {"ok": 5, "error": 1}
    assert ov["size_bytes"] == 3000
    assert ov["oldest"] == "2023-01-15T09:00:00"
    assert ov["newest"] == "2024-08-01"


def test_time_golden(fixture_db):
    t = fixture_db.get_insights()["time"]
    assert t["dated"] == 4
    assert t["per_year"] == [
        {"period": "2023", "count": 1},
        {"period": "2024", "count": 3},
    ]
    assert t["per_month"] == [
        {"period": "2023-01", "count": 1},
        {"period": "2024-07", "count": 2},
        {"period": "2024-08", "count": 1},
    ]
    assert t["busiest_month"] == {"period": "2024-07", "count": 2}
    assert t["busiest_day"] == {"period": "2024-07-04", "count": 2}


def test_places_golden(fixture_db):
    p = fixture_db.get_insights()["places"]
    assert p["located"] == 3
    assert p["coverage_pct"] == 60.0
    assert p["countries"] == [
        {"value": "Portugal", "count": 2},
        {"value": "Spain", "count": 1},
    ]
    assert p["regions"] == [{"value": "Leiria", "count": 1}]
    assert [c["value"] for c in p["cities"]] == ["Lisbon", "Madrid", "Óbidos"]


def test_content_golden(fixture_db):
    c = fixture_db.get_insights()["content"]
    # Tags are lower-cased and aggregated across ok rows only (err.jpg excluded).
    assert c["top_tags"] == [
        {"value": "beach", "count": 2},
        {"value": "family", "count": 2},
        {"value": "sunset", "count": 2},
        {"value": "indoor", "count": 1},
    ]
    assert c["unique_tags"] == 4
    assert c["scene_categories"][0] == {"value": "outdoor_travel", "count": 2}
    assert {s["value"] for s in c["scene_categories"]} == {"outdoor_travel", "people", "screenshot"}
    assert c["emotional_tones"][0] == {"value": "joyful", "count": 2}
    assert c["event_hints"] == [
        {"value": "birthday", "count": 1},
        {"value": "vacation", "count": 1},
    ]
    assert c["has_text"] == 1
    assert c["has_text_pct"] == 20.0


def test_content_top_n_is_respected(fixture_db):
    c = fixture_db.get_insights(top_n=2)["content"]
    assert len(c["top_tags"]) == 2
    assert c["unique_tags"] == 4  # the distinct count is not truncated


def test_people_golden(fixture_db):
    p = fixture_db.get_insights()["people"]
    assert p["named_persons"] == 2
    assert p["faces"] == 5
    assert [(x["label"], x["photos"]) for x in p["top_people"]] == [("Alice", 3), ("Bob", 1)]
    alice = p["top_people"][0]
    assert alice["per_year"] == [
        {"period": "2023", "count": 1},
        {"period": "2024", "count": 2},
    ]


def test_quality_golden(fixture_db):
    q = fixture_db.get_insights()["quality"]
    assert q["judged"] == 3
    assert q["coverage_pct"] == 60.0
    assert q["average"] == 6.67
    assert q["histogram"]["9"] == 1 and q["histogram"]["7"] == 1 and q["histogram"]["4"] == 1
    assert sum(q["histogram"].values()) == 3
    assert list(q["histogram"]) == [str(i) for i in range(1, 11)]
    assert [p["score"] for p in q["top_photos"]] == [9, 7, 4]
    top = q["top_photos"][0]
    assert top["reason"] == "because 9"
    assert top["scene_summary"] == "s"
    assert top["image_date"] == "2024-07-04T12:00:00"


def test_quality_top_photos_capped(fixture_db):
    q = fixture_db.get_insights(top_n=10_000)["quality"]
    assert len(q["top_photos"]) <= MAX_TOP_PHOTOS


def test_housekeeping_golden(fixture_db):
    hk = fixture_db.get_insights()["housekeeping"]
    assert hk["delete_candidates"] == {"count": 1, "bytes": 1000}
    assert hk["review_candidates"] == {"count": 1, "bytes": 0}
    assert hk["untagged"] == 1  # d.jpg (ok, tags == [])
    assert hk["errors"] == 1


def test_empty_db_reports_empty_and_omits_sections(tmp_path):
    with ProgressDB(db_path=tmp_path / "empty.db") as db:
        doc = db.get_insights()
    assert doc["empty"] is True
    assert doc["overview"]["total"] == 0
    for key in ("time", "places", "content", "people", "quality", "housekeeping"):
        assert key not in doc


def test_sections_without_data_are_omitted(tmp_path):
    """A DB with one bare ok row has content + housekeeping but nothing else."""
    with ProgressDB(db_path=tmp_path / "bare.db") as db:
        db.mark_done(Path("/x.jpg"), _img("/x.jpg", tags=["a"]))
        doc = db.get_insights()
    assert "content" in doc and "housekeeping" in doc
    for key in ("time", "places", "people", "quality"):
        assert key not in doc, key


def test_unnamed_persons_only_omits_people(tmp_path):
    with ProgressDB(db_path=tmp_path / "p.db") as db:
        db.mark_done(Path("/x.jpg"), _img("/x.jpg"))
        pid = db.create_person("")
        db._conn.execute("INSERT INTO faces (image_path, person_id) VALUES (?, ?)", ("/x.jpg", pid))
        db._conn.commit()
        assert "people" not in db.get_insights()


def test_content_tolerates_invalid_tag_json(tmp_path):
    with ProgressDB(db_path=tmp_path / "bad.db") as db:
        db.mark_done(Path("/x.jpg"), _img("/x.jpg", tags=["ok"]))
        db._conn.execute(
            "INSERT INTO processed_images (file_path, tags, status) VALUES (?, ?, 'ok')",
            ("/broken.jpg", "not json"),
        )
        db._conn.commit()
        c = db.get_insights()["content"]
    assert c["top_tags"] == [{"value": "ok", "count": 1}]


def test_quality_falls_back_to_weighted_score_when_score_null(tmp_path):
    with ProgressDB(db_path=tmp_path / "legacy.db") as db:
        db.mark_done(Path("/x.jpg"), _img("/x.jpg"))
        db._conn.execute(
            "INSERT INTO judge_scores (file_path, scored_at, weighted_score, core_score, "
            "visible_score, score) VALUES ('/x.jpg', 'now', 8, 8, 8, NULL)"
        )
        db._conn.commit()
        q = db.get_insights()["quality"]
    assert q["histogram"]["8"] == 1
    assert q["top_photos"][0]["score"] == 8


@pytest.mark.slow
def test_insights_100k_rows_under_10_seconds(tmp_path):
    """Aggregation is SQL-side, so a 100k-row synthetic DB stays fast."""
    db = ProgressDB(db_path=tmp_path / "big.db")
    conn: sqlite3.Connection = db._conn
    n = 100_000
    countries = ["Portugal", "Spain", "France", "Italy", None]
    scenes = ["outdoor_travel", "people", "food", "screenshot"]
    rows = [
        (
            f"/lib/{i:06d}.jpg",
            1000 + (i % 5000),
            json.dumps([f"tag{i % 40}", f"tag{(i * 7) % 40}"]),
            "ok" if i % 50 else "error",
            f"{2010 + i % 15}-{1 + i % 12:02d}-{1 + i % 28:02d}T10:00:00",
            countries[i % 5],
            f"city{i % 30}",
            scenes[i % 4],
            "delete" if i % 25 == 0 else None,
            i % 3 == 0,
        )
        for i in range(n)
    ]
    conn.executemany(
        "INSERT INTO processed_images (file_path, file_size, tags, status, image_date, "
        "nearest_country, nearest_city, scene_category, cleanup_class, has_text) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.executemany(
        "INSERT INTO judge_scores (file_path, scored_at, weighted_score, core_score, "
        "visible_score, score) VALUES (?, 'now', ?, ?, ?, ?)",
        [
            (f"/lib/{i:06d}.jpg", 1 + i % 10, 1 + i % 10, 1 + i % 10, 1 + i % 10)
            for i in range(0, n, 2)
        ],
    )
    conn.commit()
    try:
        start = time.perf_counter()
        doc = db.get_insights(top_n=25)
        elapsed = time.perf_counter() - start
    finally:
        db.close()
    assert doc["overview"]["total"] == n
    assert doc["quality"]["judged"] == n // 2
    assert elapsed < 10, f"insights took {elapsed:.1f}s on {n} rows"
