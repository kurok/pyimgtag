"""End to end: the real CLI picks the database from PYIMGTAG_DB (#405)."""

from __future__ import annotations

from pathlib import Path

from pyimgtag.models import ImageResult
from pyimgtag.progress_db import ProgressDB
from tests.e2e_support import run_cli


def test_status_and_tags_read_the_env_database(tmp_path: Path) -> None:
    env_db = tmp_path / "library.db"
    with ProgressDB(env_db) as db:
        img = tmp_path / "a.jpg"
        db.mark_done(img, ImageResult(file_path=str(img), file_name="a.jpg", tags=["beach"]))

    status = run_cli("status", home=tmp_path, env={"PYIMGTAG_DB": str(env_db)})
    tags = run_cli("tags", "list", home=tmp_path, env={"PYIMGTAG_DB": str(env_db)})

    assert status.returncode == 0, status.stderr
    assert "Progress: 1 / 1" in status.stdout
    assert tags.returncode == 0 and "beach" in tags.stdout
    assert not (tmp_path / ".cache" / "pyimgtag" / "progress.db").exists()


def test_the_flag_still_wins(tmp_path: Path) -> None:
    flag_db = tmp_path / "flag.db"
    ProgressDB(flag_db).close()

    result = run_cli(
        "status", "--db", str(flag_db), home=tmp_path, env={"PYIMGTAG_DB": str(tmp_path / "x.db")}
    )

    assert result.returncode == 0, result.stderr
    assert "Progress: 0 / 0" in result.stdout
    assert not (tmp_path / "x.db").exists()
