"""dedup scan on an empty library, and events that share a name (#478)."""

from __future__ import annotations

from pathlib import Path

from pyimgtag.commands.events import _folder_name
from pyimgtag.main import main
from pyimgtag.models import ImageResult
from pyimgtag.progress_db import ProgressDB


def test_dedup_scan_on_an_empty_db_says_to_run_first(tmp_path, capsys):
    db_path = tmp_path / "empty.db"
    ProgressDB(db_path=db_path).close()

    assert main(["dedup", "scan", "--db", str(db_path)]) == 0

    out = capsys.readouterr()
    assert "pyimgtag run" in out.err
    assert "unresolved group" not in out.out  # no "0 groups" that reads like "no duplicates"


def _two_days_in_zurich(tmp_path: Path) -> str:
    db_path = tmp_path / "events.db"
    with ProgressDB(db_path=db_path) as db:
        for name, date in (("a.jpg", "2024:09:01 10:00:00"), ("b.jpg", "2024:09:02 10:00:00")):
            img = tmp_path / name
            img.write_bytes(b"x")
            db.mark_done(
                img,
                ImageResult(
                    file_path=str(img),
                    file_name=name,
                    tags=["x"],
                    image_date=date,
                    nearest_city="Zürich",
                    nearest_country="Schweiz",
                ),
            )
    assert main(["events", "detect", "--db", str(db_path)]) == 0
    return str(db_path)


def _rows(out: str) -> list[str]:
    return [line for line in out.splitlines()[2:] if line.strip()]


def test_events_sharing_a_name_are_told_apart(tmp_path, capsys):
    db_path = _two_days_in_zurich(tmp_path)
    with ProgressDB(db_path=db_path) as db:
        events = db.list_events()
    assert len(events) == 2
    assert events[0]["name"] == events[1]["name"]  # the generated names collide
    capsys.readouterr()

    assert main(["events", "list", "--db", db_path]) == 0

    out = capsys.readouterr()
    names = [row[7:41].rstrip() for row in _rows(out.out)]
    assert len(set(names)) == 2
    # The older day carries its date, as its export folder does.
    assert any(name.endswith("(2024-09-01)") for name in names)
    assert "folder name" in out.err


def test_limit_does_not_change_which_row_is_suffixed(tmp_path, capsys):
    db_path = _two_days_in_zurich(tmp_path)
    capsys.readouterr()

    assert main(["events", "list", "--db", db_path, "--limit", "1"]) == 0

    (row,) = _rows(capsys.readouterr().out)
    assert not row[7:41].rstrip().endswith(")")  # the newest keeps the plain name


def test_unique_names_are_unchanged(tmp_path, capsys):
    db_path = _two_days_in_zurich(tmp_path)
    with ProgressDB(db_path=db_path) as db:
        first = db.list_events()[0]
    assert main(["events", "rename", str(first["id"]), "Lake day", "--db", db_path]) == 0
    capsys.readouterr()

    assert main(["events", "list", "--db", db_path]) == 0

    out = capsys.readouterr()
    assert "(2024-" not in out.out
    assert "folder name" not in out.err


# --- folder names: suffix chosen once, always kept whole --------------------


def _event(event_id: int, name: str, date: str = "2024-09-01 10:00:00") -> dict:
    return {"id": event_id, "name": name, "started_at": date}


def test_same_name_same_day_falls_back_to_the_id():
    used: set[str] = set()
    names = [
        _folder_name(_event(1, "Zürich"), used),
        _folder_name(_event(2, "Zürich"), used),
        _folder_name(_event(3, "Zürich"), used),
    ]
    assert names == [
        ("Zürich", ""),
        ("Zürich (2024-09-01)", " (2024-09-01)"),
        ("Zürich (3)", " (3)"),
    ]


def test_a_long_name_keeps_its_suffix_and_distinct_folders():
    long_name = "A" * 110
    used: set[str] = set()
    first, _ = _folder_name(_event(1, long_name), used)
    second, suffix = _folder_name(_event(2, long_name), used)
    assert first != second
    assert len(second) <= 100
    assert second.endswith(suffix) and suffix == " (2024-09-01)"


def test_a_name_ending_in_a_dot_gets_a_clean_suffix():
    used: set[str] = set()
    _folder_name(_event(1, "Trip to St."), used)
    folder, suffix = _folder_name(_event(2, "Trip to St."), used)
    assert folder == "Trip to St (2024-09-01)"
    assert suffix == " (2024-09-01)"


def test_names_equal_only_after_cleaning_are_suffixed_too():
    used: set[str] = set()
    _folder_name(_event(1, "A/B"), used)
    assert _folder_name(_event(2, "A-B"), used)[1] == " (2024-09-01)"


def test_dedup_scan_on_an_emptied_db_clears_the_old_plan(tmp_path, capsys):
    db_path = tmp_path / "p.db"
    with ProgressDB(db_path=db_path) as db:
        img = tmp_path / "a.jpg"
        img.write_bytes(b"x")
        db.mark_done(img, ImageResult(file_path=str(img), file_name="a.jpg", tags=["x"]))
        db.replace_unresolved_dedup_groups([("duplicate", [str(img), str(img) + "2"])], 5)
        assert db.list_dedup_groups()
        db.reset_all()

    assert main(["dedup", "scan", "--db", str(db_path)]) == 0

    with ProgressDB(db_path=db_path) as db:
        assert db.list_dedup_groups() == []
