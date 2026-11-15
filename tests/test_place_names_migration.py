"""Rows geocoded before #446 get one place name per field on upgrade (#460)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pyimgtag.db.progress_db import _first_name_update
from pyimgtag.models import ImageResult
from pyimgtag.place_names import (
    COUNTRY_SEPARATORS,
    LOCALITY_SEPARATORS,
    REGION_SEPARATORS,
    native_name,
)
from pyimgtag.progress_db import ProgressDB

_ROWS = {
    "zermatt.jpg": (
        "Zermatt",
        None,
        "Valais/Wallis",
        "Schweiz / Suisse / Svizzera / Svizra",
    ),
    "brussels.jpg": (
        None,
        "Bruxelles - Brussel",
        "Région de Bruxelles-Capitale - Brussels Hoofdstedelijk Gewest",
        "België / Belgique / Belgien",
    ),
    "biel.jpg": (None, "Biel/Bienne", "Bern/Berne", "Schweiz / Suisse / Svizzera / Svizra"),
    "paris.jpg": (None, "Paris", "Île-de-France", "France"),
}


def _old_db(tmp_path: Path) -> Path:
    """A v17 database whose rows still hold Nominatim's list forms."""
    path = tmp_path / "p.db"
    with ProgressDB(path) as db:
        for name, (place, city, region, country) in _ROWS.items():
            p = tmp_path / name
            db.mark_done(
                p,
                ImageResult(
                    file_path=str(p),
                    file_name=name,
                    nearest_place=place,
                    nearest_city=city,
                    nearest_region=region,
                    nearest_country=country,
                ),
            )
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA user_version = 17")
    return path


def _places(path: Path) -> dict[str, tuple]:
    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            "SELECT file_path, nearest_city, nearest_region, nearest_country FROM processed_images"
        ).fetchall()
    return {Path(r[0]).name: r[1:] for r in rows}


def test_upgrading_rewrites_stored_list_forms(tmp_path, monkeypatch):
    monkeypatch.setenv("PYIMGTAG_NO_MIGRATION_BACKUP", "1")
    path = _old_db(tmp_path)
    ProgressDB(path).close()  # opening migrates v17 -> v18
    assert _places(path) == {
        "zermatt.jpg": (None, "Valais", "Schweiz"),
        "brussels.jpg": ("Bruxelles - Brussel", "Région de Bruxelles-Capitale", "België"),
        # A city's double name is its signposted name: left alone, as in #446.
        "biel.jpg": ("Biel/Bienne", "Bern", "Schweiz"),
        "paris.jpg": ("Paris", "Île-de-France", "France"),
    }


def test_the_migration_runs_once_and_backs_up_first(tmp_path):
    path = _old_db(tmp_path)
    ProgressDB(path).close()
    backup = tmp_path / "p.db.bak-v17"
    assert backup.exists()
    first = backup.stat().st_mtime_ns
    migrated = _places(path)
    ProgressDB(path).close()  # reopening: nothing pending, no new backup
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 18
    assert backup.stat().st_mtime_ns == first
    assert sorted(p.name for p in tmp_path.glob("p.db.bak-*")) == ["p.db.bak-v17"]
    assert _places(path) == migrated


def test_a_data_fix_for_a_missing_column_is_skipped_not_fatal(tmp_path, monkeypatch):
    """An old table without nearest_* (only test fixtures) migrates cleanly."""
    monkeypatch.setenv("PYIMGTAG_NO_MIGRATION_BACKUP", "1")
    path = tmp_path / "bare.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE processed_images (file_path TEXT PRIMARY KEY)")
        conn.execute("PRAGMA user_version = 17")
    ProgressDB(path).close()
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 18


def test_a_misspelled_data_fix_column_is_never_silently_skipped(tmp_path):
    """Only a column that does not exist is skipped; a broken statement raises."""
    from pyimgtag.db.progress_db import _optional_column

    sql = _first_name_update("nearest_country", "country")
    assert _optional_column(sql) == ("processed_images", "nearest_country")
    assert _optional_column("UPDATE processed_images SET x = 1") is None


@pytest.mark.parametrize(
    "value",
    [
        "Schweiz / Suisse / Svizzera / Svizra",
        "België / Belgique / Belgien",
        "Valais/Wallis",
        "Trentino-Alto Adige/Südtirol",
        "Région de Bruxelles-Capitale - Brussels Hoofdstedelijk Gewest",
        "Île-de-France",
        "Provence-Alpes-Côte d'Azur",
        "Biel/Bienne",
        "Bolzano - Bozen",
        "/leading slash",
        " / leading spaced",
        "trailing / ",
        "",
        "France",
        "Schweiz\t/ Suisse",
        "Schweiz\xa0/ Suisse",
        "a\n/ b",
    ],
)
@pytest.mark.parametrize(
    ("column", "kind", "separators"),
    [
        ("nearest_country", "country", COUNTRY_SEPARATORS),
        ("nearest_region", "region", REGION_SEPARATORS),
        ("nearest_city", "locality", LOCALITY_SEPARATORS),
    ],
)
def test_the_sql_rule_equals_native_name(value, column, kind, separators):
    """The migration and the geocoder must never disagree on a name."""
    from pyimgtag.db.progress_db import _NATIVE_NAME_SQL, _native_name_sql

    conn = sqlite3.connect(":memory:")
    conn.create_function(_NATIVE_NAME_SQL, 2, _native_name_sql, deterministic=True)
    conn.execute(f"CREATE TABLE processed_images ({column} TEXT)")
    conn.execute(f"INSERT INTO processed_images VALUES (?)", (value,))  # noqa: F541
    conn.execute(_first_name_update(column, kind).split(" */ ", 1)[1])
    (stored,) = conn.execute(f"SELECT {column} FROM processed_images").fetchone()
    assert stored == native_name(value, separators)


def test_an_upgraded_database_shows_one_name_in_the_cli(tmp_path):
    """End to end: the real `pyimgtag query` on a pre-#446 database."""
    import json

    from tests.e2e_support import run_cli

    path = _old_db(tmp_path)
    result = run_cli(
        "query", "--db", str(path), "--country", "Schweiz", "--format", "json", home=tmp_path
    )
    assert result.returncode == 0, result.stderr
    countries = {row["nearest_country"] for row in json.loads(result.stdout)}
    assert countries == {"Schweiz"}
