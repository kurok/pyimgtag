"""``pyimgtag tags rename|merge|delete`` refuse unknown tags and no-op input (#399)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pyimgtag.main import main
from pyimgtag.models import ImageResult
from pyimgtag.progress_db import ProgressDB
from pyimgtag.tag_changes import TagChangeError, check_tag_change


@pytest.fixture(autouse=True)
def _no_update_check(monkeypatch):
    monkeypatch.setenv("PYIMGTAG_NO_UPDATE_CHECK", "1")


def _db(tmp_path: Path) -> str:
    db_path = tmp_path / "x.db"
    with ProgressDB(db_path) as db:
        for name, tags in (("a.jpg", ["beach", "sun"]), ("b.jpg", ["beach"])):
            img = tmp_path / name
            db.mark_done(img, ImageResult(file_path=str(img), file_name=name, tags=tags))
    return str(db_path)


def _counts(db_path: str) -> dict[str, int]:
    with ProgressDB(db_path) as db:
        return dict(db.get_tag_counts())


@pytest.mark.parametrize(
    "argv",
    [
        ["tags", "delete", "nosuchtag"],
        ["tags", "rename", "nosuchtag", "x"],
        ["tags", "merge", "nosuchtag", "beach"],
        ["tags", "delete", "nosuchtag", "--dry-run"],
        ["tags", "rename", "nosuchtag", "x", "--dry-run"],
        ["tags", "merge", "nosuchtag", "beach", "--dry-run"],
    ],
)
def test_an_unknown_tag_exits_1(tmp_path, capsys, argv):
    db_path = _db(tmp_path)
    assert main([*argv, "--db", db_path]) == 1
    err = capsys.readouterr().err
    assert "Error: no tag 'nosuchtag' (see 'pyimgtag tags list')" in err
    assert _counts(db_path) == {"beach": 2, "sun": 1}


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["tags", "merge", "beach", "beach"], "source and target are the same tag"),
        (["tags", "merge", "beach", "BEACH"], "source and target are the same tag"),
        (["tags", "rename", "beach", ""], "new tag name is empty"),
        (["tags", "rename", "beach", "   "], "new tag name is empty"),
        (["tags", "delete", ""], "tag name is empty"),
        (["tags", "merge", "a", "a"], "source and target are the same tag"),
    ],
)
def test_a_no_op_change_exits_2(tmp_path, capsys, argv, message):
    db_path = _db(tmp_path)
    assert main([*argv, "--db", db_path]) == 2
    assert f"Error: {message}" in capsys.readouterr().err
    assert _counts(db_path) == {"beach": 2, "sun": 1}


def test_existing_tags_change_as_before(tmp_path, capsys):
    db_path = _db(tmp_path)
    assert main(["tags", "rename", "beach", "shore", "--db", db_path]) == 0
    assert "Renamed 'beach' → 'shore' in 2 image(s)." in capsys.readouterr().err
    assert main(["tags", "merge", "sun", "shore", "--db", db_path]) == 0
    assert "Merged 'sun' → 'shore' in 1 image(s)." in capsys.readouterr().err
    assert main(["tags", "delete", "SHORE", "--db", db_path]) == 0
    assert "Deleted 'SHORE' from 2 image(s)." in capsys.readouterr().err
    assert _counts(db_path) == {}


def test_dry_run_output_is_unchanged_for_a_real_tag(tmp_path, capsys):
    db_path = _db(tmp_path)
    assert main(["tags", "delete", "beach", "--dry-run", "--db", db_path]) == 0
    assert capsys.readouterr().err == "[dry-run] Would delete 'beach' from 2 image(s).\n"
    assert _counts(db_path) == {"beach": 2, "sun": 1}


def test_check_reports_request_problems_before_the_lookup():
    with pytest.raises(TagChangeError) as info:
        check_tag_change([], "missing", "missing")
    assert info.value.kind == "invalid"
    with pytest.raises(TagChangeError) as info:
        check_tag_change([], "missing", "other")
    assert info.value.kind == "not_found"
    check_tag_change([("Beach", 1)], "beach", "shore")  # case-insensitive match


def test_names_are_matched_the_way_the_database_matches_them(tmp_path, capsys):
    """Lower-cased but not trimmed: a check that passes always changes images."""
    db_path = _db(tmp_path)
    # The CLI stores the new name as typed, padding included.
    assert main(["tags", "rename", "beach", " shore ", "--db", db_path]) == 0
    assert _counts(db_path) == {" shore ": 2, "sun": 1}

    # "shore" is not the stored tag: refused, rather than "deleted from 0".
    assert main(["tags", "delete", "shore", "--db", db_path]) == 1
    assert "Error: no tag 'shore'" in capsys.readouterr().err
    # Cleaning the padding is a real change, not "the same tag".
    assert main(["tags", "rename", " shore ", "shore", "--db", db_path]) == 0
    assert _counts(db_path) == {"shore": 2, "sun": 1}
    # A padded name for an existing tag is a different tag.
    assert main(["tags", "delete", " sun", "--db", db_path]) == 1
    assert _counts(db_path) == {"shore": 2, "sun": 1}
