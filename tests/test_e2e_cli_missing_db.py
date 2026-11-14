"""End to end: a mistyped --db is reported, not silently created.

Runs the real ``python -m pyimgtag`` in a child process (see
``tests/e2e_support.py``) with ``HOME`` pointed at ``tmp_path``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e_support import run_cli


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(["status"], id="status"),
        pytest.param(["tags", "list"], id="tags-list"),
        pytest.param(["query"], id="query"),
        pytest.param(["events", "list"], id="events-list"),
    ],
)
def test_a_missing_db_is_an_error_and_nothing_is_created(tmp_path: Path, argv: list[str]) -> None:
    missing = tmp_path / "typo" / "progress.db"

    result = run_cli(*argv, "--db", str(missing), home=tmp_path)

    assert result.returncode == 1
    assert f"Error: no database at {missing}." in result.stderr
    assert "Traceback" not in result.stderr
    assert not (tmp_path / "typo").exists()


def test_the_default_db_keeps_its_friendly_empty_output(tmp_path: Path) -> None:
    result = run_cli("status", home=tmp_path)

    assert result.returncode == 0, result.stderr
    assert "no database at" not in result.stderr
    assert (tmp_path / ".cache" / "pyimgtag" / "progress.db").is_file()


def test_a_writer_still_creates_the_named_db(tmp_path: Path) -> None:
    db_path = tmp_path / "fresh" / "progress.db"

    result = run_cli("tags", "rename", "old", "new", "--db", str(db_path), home=tmp_path)

    assert db_path.is_file(), result.stderr
