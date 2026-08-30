"""Tests for :mod:`pyimgtag.db.map_db` and the v14 GPS migration."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from pyimgtag.db.map_db import MAX_CELLS, MapDB, cell_size_for_zoom
from pyimgtag.models import ImageResult, JudgeResult, JudgeScores
from pyimgtag.progress_db import ProgressDB


def _result(idx, lat=None, lon=None, date=None, cleanup=None):
    path = Path(f"/img/{idx}.jpg")
    return path, ImageResult(
        file_path=str(path),
        file_name=path.name,
        tags=["x"],
        gps_lat=lat,
        gps_lon=lon,
        image_date=date,
        cleanup_class=cleanup,
        processing_status="ok",
    )


def _seed(tmp_path, rows):
    db = ProgressDB(db_path=tmp_path / "p.db")
    for row in rows:
        path, result = row
        db.mark_done(path, result)
    return db


# --- migration ---------------------------------------------------------------


def test_migration_adds_gps_columns_and_indexes(tmp_path):
    db = ProgressDB(db_path=tmp_path / "m.db")
    cols = {r[1] for r in db._conn.execute("PRAGMA table_info(processed_images)")}
    assert {"gps_lat", "gps_lon"} <= cols
    indexes = {r[1] for r in db._conn.execute("PRAGMA index_list(processed_images)")}
    assert {"idx_pi_gps", "idx_pi_image_date"} <= indexes
    assert db._conn.execute("PRAGMA user_version").fetchone()[0] >= 14


def test_migration_is_idempotent(tmp_path):
    path = tmp_path / "m.db"
    first = ProgressDB(db_path=path)
    first.mark_done(*_result(0, 1.0, 2.0))
    first.close()
    # Re-opening replays the migration guard; nothing may be lost or raised.
    second = ProgressDB(db_path=path)
    second._migrate()
    assert second.get_image(str(Path("/img/0.jpg")))["gps_lat"] == 1.0
    assert second._conn.execute("PRAGMA user_version").fetchone()[0] >= 14


def test_mark_done_persists_gps(tmp_path):
    db = _seed(tmp_path, [_result(0, 39.36, -9.15), _result(1)])
    with_gps = db.get_image(str(Path("/img/0.jpg")))
    without = db.get_image(str(Path("/img/1.jpg")))
    assert (with_gps["gps_lat"], with_gps["gps_lon"]) == (39.36, -9.15)
    assert (without["gps_lat"], without["gps_lon"]) == (None, None)


def test_update_missing_fields_backfills_gps(tmp_path):
    """The ``run --resume-from-db`` path fills coordinates it did not have."""
    db = _seed(tmp_path, [_result(0)])
    path, result = _result(0, 12.5, -3.25)
    db.update_missing_fields(path, result)
    row = db.get_image(str(path))
    assert (row["gps_lat"], row["gps_lon"]) == (12.5, -3.25)


def test_update_missing_fields_never_overwrites_gps(tmp_path):
    db = _seed(tmp_path, [_result(0, 1.0, 2.0)])
    path, result = _result(0, 50.0, 60.0)
    db.update_missing_fields(path, result)
    row = db.get_image(str(path))
    assert (row["gps_lat"], row["gps_lon"]) == (1.0, 2.0)


# --- coverage ----------------------------------------------------------------


def test_gps_coverage_counts(tmp_path):
    db = _seed(tmp_path, [_result(0, 1.0, 2.0), _result(1, 3.0, 4.0), _result(2), _result(3)])
    assert db.gps_coverage() == {"with_gps": 2, "without_gps": 2}


def test_gps_coverage_empty_db(tmp_path):
    db = ProgressDB(db_path=tmp_path / "e.db")
    assert db.gps_coverage() == {"with_gps": 0, "without_gps": 0}


# --- clusters ----------------------------------------------------------------


def test_cell_size_for_zoom_halves_and_clamps():
    assert cell_size_for_zoom(0) == 360.0
    assert cell_size_for_zoom(2) == 90.0
    assert cell_size_for_zoom(10) == pytest.approx(360.0 / 1024)
    # Out-of-range zooms clamp rather than raise.
    assert cell_size_for_zoom(-5) == cell_size_for_zoom(0)
    assert cell_size_for_zoom(99) == cell_size_for_zoom(20)


def test_clusters_group_by_zoom(tmp_path):
    db = _seed(
        tmp_path,
        [
            _result(0, 39.36, -9.15),
            _result(1, 39.37, -9.16),
            _result(2, 48.85, 2.35),
            _result(3),  # no GPS: never clustered
        ],
    )
    coarse = db.map_clusters(zoom=0)
    assert sum(c["count"] for c in coarse) == 3
    # Zoom 0 is one cell for the whole planet; by zoom 6 Portugal and Paris
    # are far apart and only the two Portuguese photos still share a cell.
    assert len(coarse) == 1
    fine = db.map_clusters(zoom=6)
    assert sorted(c["count"] for c in fine) == [1, 2]


def test_clusters_centroid_is_the_average(tmp_path):
    db = _seed(tmp_path, [_result(0, 10.0, 20.0), _result(1, 12.0, 24.0)])
    (cell,) = db.map_clusters(zoom=1)
    assert (cell["lat"], cell["lon"]) == (11.0, 22.0)
    assert cell["count"] == 2
    assert cell["sample_path"] == str(Path("/img/0.jpg"))


def test_clusters_do_not_fold_across_the_equator_or_meridian(tmp_path):
    """Truncating a signed division would merge the four quadrants at 0,0."""
    db = _seed(
        tmp_path,
        [
            _result(0, 1.0, 1.0),
            _result(1, -1.0, 1.0),
            _result(2, 1.0, -1.0),
            _result(3, -1.0, -1.0),
        ],
    )
    cells = db.map_clusters(zoom=8)
    assert len(cells) == 4
    assert all(c["count"] == 1 for c in cells)


def test_clusters_bbox_filters(tmp_path):
    db = _seed(tmp_path, [_result(0, 39.36, -9.15), _result(1, 48.85, 2.35)])
    inside = db.map_clusters(bbox=(39.0, -10.0, 40.0, -9.0), zoom=6)
    assert [c["count"] for c in inside] == [1]
    assert inside[0]["sample_path"] == str(Path("/img/0.jpg"))
    # Latitudes are normalised, so swapping them describes the same box.
    # Longitude order is *not* normalised: it is what distinguishes a normal
    # box from one crossing the antimeridian.
    assert db.map_clusters(bbox=(40.0, -10.0, 39.0, -9.0), zoom=6) == inside


def test_clusters_bbox_crossing_the_antimeridian(tmp_path):
    db = _seed(
        tmp_path, [_result(0, -15.0, 175.0), _result(1, -15.0, -175.0), _result(2, 0.0, 0.0)]
    )
    cells = db.map_clusters(bbox=(-20.0, 170.0, -10.0, -170.0), zoom=4)
    assert sum(c["count"] for c in cells) == 2


def test_clusters_respect_the_cap(tmp_path):
    db = _seed(tmp_path, [_result(i, float(i % 80) - 40.0, float(i) - 100.0) for i in range(60)])
    capped = db.map_clusters(zoom=12, limit=5)
    assert len(capped) == 5
    # A caller cannot ask for more than the module-level ceiling.
    assert len(db.map_clusters(zoom=12, limit=MAX_CELLS * 10)) <= MAX_CELLS


def test_clusters_empty_db(tmp_path):
    assert ProgressDB(db_path=tmp_path / "e.db").map_clusters(zoom=3) == []


# --- timeline ----------------------------------------------------------------


def test_timeline_months_golden_counts(tmp_path):
    db = _seed(
        tmp_path,
        [
            _result(0, date="2024-03-17T10:00:00"),
            _result(1, date="2024-03-18T10:00:00"),
            _result(2, date="2024-04-01T10:00:00"),
            _result(3, date="2023-12-25T10:00:00"),
            _result(4),  # undated rows are absent from the histogram
        ],
    )
    months = db.timeline_months()
    assert [(m["period"], m["count"]) for m in months] == [
        ("2023-12", 1),
        ("2024-03", 2),
        ("2024-04", 1),
    ]
    assert all(m["avg_judge_score"] is None for m in months)


def test_timeline_months_carry_judge_and_cleanup_metrics(tmp_path):
    db = _seed(
        tmp_path,
        [
            _result(0, date="2024-03-01T00:00:00", cleanup="delete"),
            _result(1, date="2024-03-02T00:00:00", cleanup="keep"),
        ],
    )
    for idx, score in ((0, 4), (1, 8)):
        path = Path(f"/img/{idx}.jpg")
        db.save_judge_result(
            JudgeResult(
                file_path=str(path),
                file_name=path.name,
                weighted_score=score,
                core_score=score,
                visible_score=score,
                scores=JudgeScores(score=score, reason="r"),
            )
        )
    (march,) = db.timeline_months()
    assert march["avg_judge_score"] == 6.0
    assert march["cleanup"] == {"delete": 1, "review": 0, "keep": 1}


def test_timeline_days_golden_counts(tmp_path):
    db = _seed(
        tmp_path,
        [
            _result(0, date="2024-03-17T10:00:00"),
            _result(1, date="2024-03-17T11:00:00"),
            _result(2, date="2024-03-19T10:00:00"),
            _result(3, date="2024-04-17T10:00:00"),
        ],
    )
    days = db.timeline_days("2024-03")
    assert [(d["period"], d["count"]) for d in days] == [("2024-03-17", 2), ("2024-03-19", 1)]


def test_timeline_days_rejects_a_malformed_month(tmp_path):
    db = _seed(tmp_path, [_result(0, date="2024-03-17T10:00:00")])
    assert db.timeline_days("2024") == []
    assert db.timeline_days("") == []


def test_timeline_empty_db(tmp_path):
    db = ProgressDB(db_path=tmp_path / "e.db")
    assert db.timeline_months() == []
    assert db.timeline_days("2024-03") == []


# --- performance -------------------------------------------------------------


@pytest.mark.slow
def test_clusters_stay_fast_on_a_100k_row_db(tmp_path):
    """Budget check for AC "responds < 200 ms for a 100k-row synthetic DB".

    The assertion allows 2.0 s — a 10x cushion over the 200 ms target — so a
    loaded or throttled CI runner cannot make this flake while a genuine
    regression (a dropped index, per-row Python work) still trips it.
    """
    db = ProgressDB(db_path=tmp_path / "big.db")
    rows = [
        (
            f"/img/{i}.jpg",
            (i % 17_000) / 100.0 - 85.0,
            (i % 35_000) / 100.0 - 175.0,
            "2024-03-17T10:00:00",
        )
        for i in range(100_000)
    ]
    db._conn.executemany(
        "INSERT INTO processed_images "
        "(file_path, status, gps_lat, gps_lon, image_date) VALUES (?, 'ok', ?, ?, ?)",
        rows,
    )
    db._conn.commit()

    start = time.perf_counter()
    cells = db.map_clusters(bbox=(-60.0, -120.0, 60.0, 120.0), zoom=8)
    elapsed = time.perf_counter() - start
    assert cells, "the synthetic library must produce clusters"
    assert sum(c["count"] for c in cells) > 0
    assert elapsed < 2.0, f"cluster query took {elapsed:.3f}s"


def test_mapdb_can_be_used_directly(tmp_path):
    """The domain helper works standalone, like the other db/ classes."""
    db = _seed(tmp_path, [_result(0, 1.0, 2.0)])
    helper = MapDB(db._conn)
    assert helper.gps_coverage()["with_gps"] == 1
    assert len(helper.clusters(zoom=4)) == 1
