"""Place filters find the stored native name from English, codes and unaccented input (#474)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pyimgtag.main import main
from pyimgtag.models import ImageResult
from pyimgtag.place_names import fold, place_like_patterns
from pyimgtag.progress_db import ProgressDB

_PLACES = [
    ("zurich.jpg", "Zürich", "Schweiz"),
    ("munich.jpg", "München", "Deutschland"),
    ("vienna.jpg", "Wien", "Österreich"),
    ("sydney.jpg", "Sydney", "Australia"),
    ("ny.jpg", "New York", "United States"),
    ("minsk.jpg", "Мінск", "Беларусь"),
]


def _library(tmp_path: Path) -> str:
    db_path = tmp_path / "test.db"
    with ProgressDB(db_path=db_path) as db:
        for name, city, country in _PLACES:
            img = tmp_path / name
            img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 16)
            db.mark_done(
                img,
                ImageResult(
                    file_path=str(img),
                    file_name=name,
                    tags=["x"],
                    nearest_city=city,
                    nearest_country=country,
                ),
            )
    return str(db_path)


def _names(db_path: str, **filters: str) -> list[str]:
    with ProgressDB(db_path=db_path) as db:
        return sorted(Path(r["file_path"]).name for r in db.query_images(**filters))


class TestFold:
    def test_drops_case_and_accents_in_either_unicode_form(self):
        assert fold("Zürich") == fold("Zürich") == fold("ZURICH") == "zurich"

    def test_none_passes_through(self):
        assert fold(None) is None


class TestCountryFilter:
    @pytest.mark.parametrize(
        "typed", ["Schweiz", "schweiz", "Switzerland", "SWITZERLAND", "CH", "ch", "Suisse"]
    )
    def test_native_english_code_and_other_language_all_match(self, tmp_path, typed):
        assert _names(_library(tmp_path), country=typed) == ["zurich.jpg"]

    def test_unaccented_native_name_matches(self, tmp_path):
        assert _names(_library(tmp_path), country="osterreich") == ["vienna.jpg"]

    def test_a_code_is_not_a_substring(self, tmp_path):
        # "us" is inside "Australia" and "Беларусь" is not, but neither must match US.
        assert _names(_library(tmp_path), country="US") == ["ny.jpg"]

    def test_a_short_alias_is_not_a_substring(self, tmp_path):
        assert _names(_library(tmp_path), country="USA") == ["ny.jpg"]

    def test_unknown_name_is_still_a_substring(self, tmp_path):
        assert _names(_library(tmp_path), country="stral") == ["sydney.jpg"]

    def test_a_stored_code_is_still_found_by_that_code(self, tmp_path):
        db_path = tmp_path / "codes.db"
        img = tmp_path / "kyiv.jpg"
        img.write_bytes(b"x")
        with ProgressDB(db_path=db_path) as db:
            db.mark_done(img, ImageResult(file_path=str(img), file_name="kyiv.jpg", tags=["x"]))
            db._conn.execute("UPDATE processed_images SET nearest_country = 'UA'")
            db._conn.commit()
        assert _names(str(db_path), country="UA") == ["kyiv.jpg"]

    def test_like_wildcards_in_input_are_literal(self, tmp_path):
        assert _names(_library(tmp_path), country="%") == []


class TestCityFilter:
    @pytest.mark.parametrize(("typed", "expected"), [("Munich", "munich.jpg"), ("Vienna", "vienna.jpg")])
    def test_english_exonym_matches(self, tmp_path, typed, expected):
        assert _names(_library(tmp_path), city=typed) == [expected]

    def test_unaccented_city_matches(self, tmp_path):
        assert _names(_library(tmp_path), city="Zurich") == ["zurich.jpg"]

    def test_non_ascii_case_folds(self, tmp_path):
        assert _names(_library(tmp_path), city="МІНСК") == ["minsk.jpg"]


def test_patterns_for_a_code_and_a_name():
    assert place_like_patterns("CH", "country") == ["ch", "%schweiz%"]
    assert place_like_patterns("Switzerland", "country") == ["%switzerland%", "%schweiz%"]


class TestNoMatchHint:
    def test_lists_the_countries_in_the_library(self, tmp_path, capsys):
        db_path = _library(tmp_path)

        assert main(["query", "--db", db_path, "--country", "Narnia"]) == 0

        err = capsys.readouterr().err
        assert 'No country matched "Narnia"' in err
        for country in ("Schweiz", "Deutschland", "Österreich", "United States"):
            assert country in err

    def test_lists_the_cities_and_caps_the_list(self, tmp_path, capsys, monkeypatch):
        import pyimgtag.commands.query as query_mod

        monkeypatch.setattr(query_mod, "_PLACES_SHOWN", 2)
        db_path = _library(tmp_path)

        assert main(["query", "--db", db_path, "--city", "Atlantis"]) == 0

        err = capsys.readouterr().err
        assert 'No city matched "Atlantis"' in err
        assert f"(and {len(_PLACES) - 2} more)" in err

    def test_no_hint_when_the_place_filter_was_not_the_problem(self, tmp_path, capsys):
        db_path = _library(tmp_path)

        assert main(["query", "--db", db_path, "--tag", "nothing-like-this"]) == 0

        assert "The library has" not in capsys.readouterr().err

    def test_no_hint_when_the_place_matches_but_another_filter_does_not(self, tmp_path, capsys):
        db_path = _library(tmp_path)

        assert main(["query", "--db", db_path, "--country", "CH", "--tag", "none"]) == 0

        err = capsys.readouterr().err
        assert "No images matched" in err
        assert "No country matched" not in err
