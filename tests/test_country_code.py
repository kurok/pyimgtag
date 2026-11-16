"""The ISO country code is stored with the place, so --country KE needs no name table."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pyimgtag.db.progress_db import SCHEMA_VERSION
from pyimgtag.geocoder import ReverseGeocoder
from pyimgtag.main import main
from pyimgtag.models import ImageResult
from pyimgtag.place_names import country_code, place_matches, typed_country_codes
from pyimgtag.progress_db import ProgressDB


def _no_network(*args, **kwargs):
    raise AssertionError("no network call expected")


def _row(db: ProgressDB, tmp_path: Path, name: str, country: str | None, code: str | None) -> None:
    img = tmp_path / name
    img.write_bytes(b"x")
    db.mark_done(
        img,
        ImageResult(
            file_path=str(img),
            file_name=name,
            tags=["x"],
            nearest_country=country,
            nearest_country_code=code,
        ),
    )


def _names(db_path: Path, **filters: str) -> list[str]:
    with ProgressDB(db_path=db_path) as db:
        return sorted(Path(r["file_path"]).name for r in db.query_images(**filters))


@pytest.fixture
def library(tmp_path: Path) -> Path:
    db_path = tmp_path / "lib.db"
    with ProgressDB(db_path=db_path) as db:
        _row(db, tmp_path, "nairobi.jpg", "Kenya", "ke")
        _row(db, tmp_path, "zurich.jpg", "Schweiz", "ch")
        _row(db, tmp_path, "sydney.jpg", "Australia", "au")
        _row(db, tmp_path, "london.jpg", "United Kingdom", "gb")
        # Geocoded before the code was stored: only the name is there.
        _row(db, tmp_path, "bern.jpg", "Schweiz", None)
    return db_path


class TestCountryCodeValue:
    @pytest.mark.parametrize(("raw", "expected"), [("ke", "ke"), (" KE ", "ke"), ("CH", "ch")])
    def test_two_letters_are_kept_lowercase(self, raw, expected):
        assert country_code(raw) == expected

    @pytest.mark.parametrize("raw", [None, "", "kenya", "k", "1a", "кe", 42])
    def test_anything_else_is_none(self, raw):
        assert country_code(raw) is None


class TestTypedCodes:
    def test_a_code_is_itself(self):
        assert typed_country_codes("KE") == {"ke"}

    def test_a_short_alias_adds_its_country_code(self):
        assert typed_country_codes("UK") == {"uk", "gb"}

    def test_a_name_is_not_a_code(self):
        assert typed_country_codes("Kenya") == frozenset()
        assert typed_country_codes("USA") == frozenset()


class TestPlaceMatches:
    def test_a_code_matches_the_stored_code_with_no_table_entry(self):
        assert place_matches("Kenya", "KE", "country", "ke")

    def test_a_stored_code_decides_for_a_typed_code(self):
        # Without the code "CH" would find "Schweiz" by alias; the code says no.
        assert not place_matches("Schweiz", "CH", "country", "li")

    def test_a_name_is_still_matched_by_name_when_a_code_is_stored(self):
        assert place_matches("Kenya", "kenya", "country", "ke")

    def test_without_a_stored_code_the_alias_table_answers(self):
        assert place_matches("Schweiz", "CH", "country", None)
        assert not place_matches("Kenya", "KE", "country", None)

    def test_a_row_with_only_a_code_matches_its_code(self):
        assert place_matches(None, "KE", "country", "ke")

    def test_city_ignores_the_code(self):
        assert not place_matches("Nairobi", "KE", "city", "ke")


class TestQueryByCode:
    def test_kenya_is_found_by_its_code(self, library):
        assert _names(library, country="KE") == ["nairobi.jpg"]
        assert _names(library, country="ke") == ["nairobi.jpg"]

    def test_a_code_finds_coded_rows_and_falls_back_for_old_ones(self, library):
        assert _names(library, country="CH") == ["bern.jpg", "zurich.jpg"]

    def test_a_short_alias_matches_the_stored_code(self, library):
        assert _names(library, country="UK") == ["london.jpg"]

    def test_a_code_is_never_a_substring(self, library):
        assert _names(library, country="US") == []

    def test_names_keep_working(self, library):
        assert _names(library, country="Kenya") == ["nairobi.jpg"]
        assert _names(library, country="Switzerland") == ["bern.jpg", "zurich.jpg"]

    def test_query_cli(self, library, capsys):
        assert main(["query", "--db", str(library), "--country", "KE", "--format", "json"]) == 0
        out = capsys.readouterr().out
        rows = json.loads(out)
        assert [Path(r["file_path"]).name for r in rows] == ["nairobi.jpg"]


class TestDbWrites:
    def test_mark_done_stores_the_code(self, library):
        with sqlite3.connect(library) as conn:
            got = dict(
                conn.execute("SELECT nearest_country, nearest_country_code FROM processed_images")
            )
        assert got["Kenya"] == "ke"

    def test_update_missing_fields_fills_a_missing_code_only(self, tmp_path):
        db_path = tmp_path / "u.db"
        with ProgressDB(db_path=db_path) as db:
            _row(db, tmp_path, "a.jpg", "Kenya", None)
            _row(db, tmp_path, "b.jpg", "Kenya", "ke")
            for name in ("a.jpg", "b.jpg"):
                db.update_missing_fields(tmp_path / name, ImageResult(nearest_country_code="tz"))
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT file_path, nearest_country_code FROM processed_images"
            ).fetchall()
        assert {Path(p).name: c for p, c in rows} == {"a.jpg": "tz", "b.jpg": "ke"}


class TestMigration:
    def _v18_db(self, tmp_path: Path) -> Path:
        path = tmp_path / "old.db"
        with ProgressDB(path) as db:
            _row(db, tmp_path, "zurich.jpg", "Schweiz", None)
        with sqlite3.connect(path) as conn:
            conn.execute("ALTER TABLE processed_images DROP COLUMN nearest_country_code")
            conn.execute("PRAGMA user_version = 18")
        return path

    def test_v19_adds_an_empty_code_column(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PYIMGTAG_NO_MIGRATION_BACKUP", "1")
        path = self._v18_db(tmp_path)
        with patch("requests.Session.get", side_effect=_no_network):
            ProgressDB(path).close()
        with sqlite3.connect(path) as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 19
            rows = conn.execute(
                "SELECT nearest_country, nearest_country_code FROM processed_images"
            ).fetchall()
        assert rows == [("Schweiz", None)]

    def test_an_old_row_still_matches_a_code_through_the_alias_table(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PYIMGTAG_NO_MIGRATION_BACKUP", "1")
        path = self._v18_db(tmp_path)
        assert _names(path, country="CH") == ["zurich.jpg"]


def _reply(address: dict) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {"address": address}
    resp.raise_for_status.return_value = None
    return resp


class TestGeocoderCache:
    def test_a_lookup_carries_the_code_and_the_cache_keeps_it(self, tmp_path):
        geo = ReverseGeocoder(cache_dir=tmp_path)
        reply = _reply({"city": "Nairobi", "country": "Kenya", "country_code": "ke"})
        with patch.object(geo._session, "get", return_value=reply), patch("time.sleep"):
            fresh = geo.resolve(-1.2921, 36.8219)
        assert fresh.nearest_country_code == "ke"

        cache = json.loads((tmp_path / "geocode_cache.json").read_text(encoding="utf-8"))
        assert cache["-1.29,36.82"]["v"]["nearest_country_code"] == "ke"

        reopened = ReverseGeocoder(cache_dir=tmp_path)
        with patch.object(reopened._session, "get", side_effect=_no_network):
            hit = reopened.resolve(-1.2921, 36.8219)
        assert (hit.nearest_country, hit.nearest_country_code) == ("Kenya", "ke")

    def test_an_entry_written_before_the_code_still_loads(self, tmp_path):
        import time

        old = {
            "47.37,8.54": {
                "v": {
                    "nearest_place": None,
                    "nearest_city": "Zürich",
                    "nearest_region": "Zürich",
                    "nearest_country": "Schweiz",
                },
                "ts": time.time(),
            }
        }
        (tmp_path / "geocode_cache.json").write_text(json.dumps(old), encoding="utf-8")
        geo = ReverseGeocoder(cache_dir=tmp_path)
        with patch.object(geo._session, "get", side_effect=_no_network):
            hit = geo.resolve(47.3712, 8.5412)
        assert hit.error is None
        assert (hit.nearest_country, hit.nearest_country_code) == ("Schweiz", None)

    def test_a_malformed_code_is_dropped(self, tmp_path):
        geo = ReverseGeocoder(cache_dir=tmp_path)
        reply = _reply({"country": "Kenya", "country_code": "kenya"})
        with patch.object(geo._session, "get", return_value=reply), patch("time.sleep"):
            assert geo.resolve(-1.29, 36.82).nearest_country_code is None
