"""Tests for the ``query`` terminal table: the PATH column keeps the end of a path."""

from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest

from pyimgtag.main import main
from pyimgtag.models import ImageResult
from pyimgtag.progress_db import ProgressDB
from pyimgtag.text_fit import display_width, elide_path, pad


def _assert_starts_on_a_boundary(path: str, shown: str) -> None:
    kept = shown[1:]
    assert shown.startswith("…")
    before = path[len(path) - len(kept) - 1]
    assert kept[0] in "/\\" or before in "/\\ _-", shown


class TestElidePath:
    def test_short_path_is_unchanged(self):
        assert elide_path("/lib/a.jpg", 50) == "/lib/a.jpg"

    def test_exact_fit_is_unchanged(self):
        path = "/" + "a" * 49
        assert elide_path(path, 50) == path

    def test_long_path_keeps_the_end_after_an_ellipsis(self):
        path = "/lab/library/Phone Import 2024/IMG_20240703_081122 (1).jpg"
        shown = elide_path(path, 50)
        assert shown == "…/Phone Import 2024/IMG_20240703_081122 (1).jpg"
        assert len(shown) <= 50

    def test_cut_starts_at_a_separator_not_mid_word(self):
        path = "/Users/someone/Pictures/Phone Import 2024/IMG_1.jpg"
        for width in range(12, len(path)):
            shown = elide_path(path, width)
            assert len(shown) <= width
            assert path.endswith(shown[1:])
            _assert_starts_on_a_boundary(path, shown)

    def test_windows_separator(self):
        path = "C:\\Users\\someone\\Pictures\\Phone Import 2024\\IMG_1.jpg"
        assert elide_path(path, 30) == "…\\Phone Import 2024\\IMG_1.jpg"

    def test_file_name_longer_than_column_keeps_its_tail(self):
        path = "/a/" + "x" * 60 + ".jpg"
        shown = elide_path(path, 20)
        assert len(shown) == 20
        assert shown.startswith("…")
        assert shown.endswith(".jpg")

    def test_one_long_folder_past_the_cut_is_not_thrown_away(self):
        path = "/x/A_very_long_album_folder_name_2024/a.jpg"
        shown = elide_path(path, 40)
        assert len(shown) <= 40
        assert shown.endswith("folder_name_2024/a.jpg")
        _assert_starts_on_a_boundary(path, shown)

    def test_never_starts_on_a_combining_mark(self):
        # "Zürich" as macOS stores it (NFD): u + U+0308.
        name = "Zu\u0308rich.jpg"
        path = "/" + name
        for width in range(2, len(path)):
            shown = elide_path(path, width)
            assert display_width(shown) <= width
            assert not unicodedata.combining(shown[1:2] or "a")

    @pytest.mark.parametrize(
        "path",
        [
            "/Users/someone/Pictures/Zu\u0308rich Zu\u0308rich Zu\u0308rich/IMG_1.jpg",
            "/Users/someone/写真/東京 2024 旅行 アルバム/IMG_1.jpg",
        ],
    )
    def test_column_is_measured_in_display_cells(self, path):
        shown = elide_path(path, 30)
        assert display_width(shown) <= 30
        assert display_width(pad(shown, 30)) == 30
        assert path.endswith(shown[1:])

    @pytest.mark.parametrize(("width", "expected"), [(1, "…"), (0, "")])
    def test_tiny_width(self, width, expected):
        assert elide_path("/abc/def.jpg", width) == expected


def _db_with(tmp_path: Path, tags: list[str], folder: str = "lib") -> tuple[str, Path]:
    img_dir = tmp_path / folder
    img_dir.mkdir(parents=True)
    img = img_dir / "IMG_20240703_081122 (1).jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 16)
    db_path = tmp_path / "test.db"
    with ProgressDB(db_path=db_path) as db:
        db.mark_done(img, ImageResult(file_path=str(img), file_name=img.name, tags=tags))
    return str(db_path), img


class TestQueryTable:
    def test_long_path_row_starts_with_ellipsis(self, tmp_path, capsys):
        db_path, img = _db_with(tmp_path, ["sunset"], folder="library/Phone Import 2024")
        assert len(str(img)) > 50

        assert main(["query", "--db", db_path]) == 0
        path_col = capsys.readouterr().out.splitlines()[2][:50].rstrip()
        assert str(img).endswith(path_col[1:])
        _assert_starts_on_a_boundary(str(img), path_col)
        assert path_col.endswith(img.name)

    def test_long_tag_list_is_marked(self, tmp_path, capsys):
        db_path, _ = _db_with(tmp_path, [f"tag{i:02d}" for i in range(20)])

        assert main(["query", "--db", db_path]) == 0
        tags_col = capsys.readouterr().out.splitlines()[2][52:92].rstrip()
        assert tags_col.endswith("…")
        assert not tags_col[:-1].endswith((",", " "))
