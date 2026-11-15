"""End to end: `dedup undo --forget-missing` for a quarantine deleted on purpose (#458).

The real CLI: resolve into a quarantine, delete it, see plain undo keep the
records and name the flag, then forget them and get the group back.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from pyimgtag.models import ImageResult
from pyimgtag.progress_db import ProgressDB
from tests.e2e_support import run_cli


def _library(tmp_path: Path) -> Path:
    db_path = tmp_path / "p.db"
    paths = []
    with ProgressDB(db_path) as db:
        for name, size in (("keep.jpg", 900), ("a.jpg", 100), ("b.jpg", 100)):
            p = tmp_path / "lib" / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"x" * size)
            db.mark_done(p, ImageResult(file_path=str(p), file_name=name, phash="ffffffffffffffff"))
            paths.append(str(p))
        db.replace_unresolved_dedup_groups([("duplicate", paths)], 5)
    return db_path


def test_forget_missing_end_to_end(tmp_path: Path) -> None:
    db = _library(tmp_path)
    quarantine = tmp_path / "q"
    resolved = run_cli(
        "dedup", "resolve", "--move-to", str(quarantine), "--db", str(db), home=tmp_path
    )
    assert resolved.returncode == 0, resolved.stderr
    shutil.rmtree(quarantine)

    plain = run_cli("dedup", "undo", "--db", str(db), home=tmp_path)
    assert plain.returncode == 1
    assert "--forget-missing" in plain.stderr

    no_group = run_cli("dedup", "undo", "--forget-missing", "--db", str(db), home=tmp_path)
    assert no_group.returncode == 2

    with ProgressDB(db) as progress:
        group_id = progress.list_dedup_groups(include_resolved=True)[0]["id"]
    forgot = run_cli(
        "dedup", "undo", "--group", str(group_id), "--forget-missing", "--db", str(db),
        home=tmp_path,
    )  # fmt: skip
    assert forgot.returncode == 0, forgot.stderr
    assert "Forgot 2 copy/copies" in forgot.stdout

    listed = run_cli("dedup", "list", "--db", str(db), home=tmp_path)
    assert listed.returncode == 0
    assert "keep.jpg" in listed.stdout  # the group is planned again
