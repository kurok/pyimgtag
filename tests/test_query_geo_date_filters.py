"""Tests for the ``--bbox`` / ``--year`` / ``--month`` / ``day`` query filters.

Covers the parsing helpers in :mod:`pyimgtag.filters`, the SQL they drive
in :meth:`pyimgtag.db.image_db.ImageDB.query_images`, and that the new
filters compose with the pre-existing ones.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pyimgtag.filters import parse_bbox, parse_day, parse_month, parse_year
from pyimgtag.models import ImageResult
from pyimgtag.progress_db import ProgressDB

# (index, lat, lon, image_date, tag, city)
_FIXTURE = [
    (0, 39.36, -9.15, "2024-03-17T10:00:00", "beach", "Obidos"),
    (1, 39.37, -9.16, "2024-03-18T10:00:00", "beach", "Obidos"),
    (2, 48.85, 2.35, "2023-07-04T09:00:00", "city", "Paris"),
    (3, -15.0, 175.0, "2024-03-01T00:00:00", "sea", "Suva"),
    (4, -15.0, -175.0, "2024-03-02T00:00:00", "sea", "Apia"),
    (5, None, None, "2024-12-25T00:00:00", "indoor", None),
]


@pytest.fixture()
def db(tmp_path):
    progress = ProgressDB(db_path=tmp_path / "p.db")
    for idx, lat, lon, date, tag, city in _FIXTURE:
        path = Path(f"/img/{idx}.jpg")
        progress.mark_done(
            path,
            ImageResult(
                file_path=str(path),
                file_name=path.name,
                tags=[tag],
                gps_lat=lat,
                gps_lon=lon,
                image_date=date,
                nearest_city=city,
                processing_status="ok",
            ),
        )
    return progress


def _names(rows) -> list[str]:
    return sorted(r["file_name"] for r in rows)


# --- parsing -----------------------------------------------------------------


def test_parse_bbox_accepts_whitespace_and_ints():
    assert parse_bbox(" 39, -9 , 40,-8 ") == (39.0, -9.0, 40.0, -8.0)


def test_parse_bbox_preserves_longitude_order():
    """lon1 > lon2 is meaningful, so the parser must not sort it away."""
    assert parse_bbox("-20,170,-10,-170") == (-20.0, 170.0, -10.0, -170.0)


@pytest.mark.parametrize(
    "value,message",
    [
        ("1,2,3", "4 comma-separated"),
        ("1,2,3,4,5", "4 comma-separated"),
        ("a,b,c,d", "must be numbers"),
        ("91,0,0,0", "lat1 must be between -90 and 90"),
        ("0,0,-90.5,0", "lat2 must be between -90 and 90"),
        ("0,181,0,0", "lon1 must be between -180 and 180"),
        ("0,0,0,-180.5", "lon2 must be between -180 and 180"),
    ],
)
def test_parse_bbox_errors_are_specific(value, message):
    with pytest.raises(ValueError, match=message):
        parse_bbox(value)


@pytest.mark.parametrize("parser,good", [(parse_year, "2024"), (parse_month, "2024-03")])
def test_date_parsers_accept_valid_values(parser, good):
    assert parser(f" {good} ") == good


@pytest.mark.parametrize(
    "parser,bad",
    [
        (parse_year, "24"),
        (parse_year, "2024-03"),
        (parse_month, "2024"),
        (parse_month, "2024-13"),
        (parse_month, "2024-00"),
        (parse_month, "2024-3"),
        (parse_day, "2024-03"),
        (parse_day, "2024-03-32"),
        (parse_day, "2024-03-00"),
    ],
)
def test_date_parsers_reject_bad_values(parser, bad):
    with pytest.raises(ValueError):
        parser(bad)


def test_parse_day_accepts_a_valid_day():
    assert parse_day("2024-03-17") == "2024-03-17"


# --- bbox --------------------------------------------------------------------


def test_bbox_selects_only_photos_inside(db):
    rows = db.query_images(bbox=(39.0, -10.0, 40.0, -9.0))
    assert _names(rows) == ["0.jpg", "1.jpg"]


def test_bbox_is_inclusive_on_the_edges(db):
    rows = db.query_images(bbox=(39.36, -9.15, 39.36, -9.15))
    assert _names(rows) == ["0.jpg"]


def test_bbox_normalises_latitude_order(db):
    assert db.query_images(bbox=(40.0, -10.0, 39.0, -9.0)) == db.query_images(
        bbox=(39.0, -10.0, 40.0, -9.0)
    )


def test_bbox_crossing_the_antimeridian(db):
    """lon1 > lon2 selects the short way round 180°, not its complement."""
    rows = db.query_images(bbox=(-20.0, 170.0, -10.0, -170.0))
    assert _names(rows) == ["3.jpg", "4.jpg"]


def test_bbox_not_crossing_the_antimeridian_takes_the_long_way(db):
    rows = db.query_images(bbox=(-20.0, -170.0, -10.0, 170.0))
    assert _names(rows) == []


def test_bbox_excludes_rows_without_coordinates(db):
    rows = db.query_images(bbox=(-90.0, -180.0, 90.0, 180.0))
    assert "5.jpg" not in _names(rows)


def test_query_row_exposes_coordinates(db):
    (row,) = db.query_images(bbox=(39.36, -9.15, 39.36, -9.15))
    assert (row["gps_lat"], row["gps_lon"]) == (39.36, -9.15)


# --- date prefixes -----------------------------------------------------------


def test_year_filter(db):
    assert _names(db.query_images(date_prefix="2024")) == [
        "0.jpg",
        "1.jpg",
        "3.jpg",
        "4.jpg",
        "5.jpg",
    ]
    assert _names(db.query_images(date_prefix="2023")) == ["2.jpg"]


def test_month_filter(db):
    assert _names(db.query_images(date_prefix="2024-03")) == [
        "0.jpg",
        "1.jpg",
        "3.jpg",
        "4.jpg",
    ]


def test_day_filter(db):
    assert _names(db.query_images(date_prefix="2024-03-17")) == ["0.jpg"]


def test_date_prefix_does_not_leak_into_the_next_period(db):
    """A prefix range must stop at the boundary, not spill into 2025 or April."""
    assert db.query_images(date_prefix="2025") == []
    assert db.query_images(date_prefix="2024-04") == []


# --- composition -------------------------------------------------------------


def test_bbox_composes_with_tag_and_city(db):
    rows = db.query_images(bbox=(39.0, -10.0, 40.0, -9.0), tag="beach", city="obidos")
    assert _names(rows) == ["0.jpg", "1.jpg"]
    assert db.query_images(bbox=(39.0, -10.0, 40.0, -9.0), tag="city") == []


def test_bbox_composes_with_a_date_prefix(db):
    rows = db.query_images(bbox=(39.0, -10.0, 40.0, -9.0), date_prefix="2024-03-18")
    assert _names(rows) == ["1.jpg"]


def test_date_prefix_composes_with_limit_and_sort(db):
    rows = db.query_images(date_prefix="2024-03", sort="shot_asc", limit=2)
    assert [r["file_name"] for r in rows] == ["3.jpg", "4.jpg"]


def test_no_filters_returns_everything(db):
    assert len(db.query_images()) == len(_FIXTURE)
