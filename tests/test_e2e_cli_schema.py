"""End to end: the real CLI refuses a newer schema and backs up before migrating.

Each test runs ``python -m pyimgtag`` in a child process against a database in
``tmp_path`` (see ``tests/e2e_support.py``).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from pyimgtag.db.progress_db import SCHEMA_VERSION
from tests.e2e_support import run_cli


def _set_user_version(path: Path, version: int) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(f"PRAGMA user_version = {int(version)}")
        conn.commit()
    finally:
        conn.close()


def _user_version(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def _old_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE processed_images (file_path TEXT PRIMARY KEY, status TEXT, tags TEXT)"
        )
        conn.execute("INSERT INTO processed_images VALUES ('/photos/a.jpg', 'ok', '[\"x\"]')")
        conn.execute("PRAGMA user_version = 15")
        conn.commit()
    finally:
        conn.close()


def test_status_refuses_a_database_from_a_newer_pyimgtag(tmp_path: Path) -> None:
    db_path = tmp_path / "progress.db"
    _old_db(db_path)
    _set_user_version(db_path, SCHEMA_VERSION + 1)

    result = run_cli("status", "--db", str(db_path), home=tmp_path)

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert f"has schema v{SCHEMA_VERSION + 1}" in result.stderr
    assert f"supports up to v{SCHEMA_VERSION}" in result.stderr
    assert "Upgrade pyimgtag, or point --db at another file." in result.stderr
    assert _user_version(db_path) == SCHEMA_VERSION + 1


def test_status_backs_up_an_old_database_before_migrating(tmp_path: Path) -> None:
    db_path = tmp_path / "progress.db"
    _old_db(db_path)

    result = run_cli("status", "--db", str(db_path), home=tmp_path)

    assert result.returncode == 0, result.stderr
    backup = tmp_path / "progress.db.bak-v15"
    assert backup.exists()
    assert _user_version(backup) == 15
    assert _user_version(db_path) == SCHEMA_VERSION

    # A second run finds nothing to migrate and writes no further backup.
    again = run_cli("status", "--db", str(db_path), home=tmp_path)
    assert again.returncode == 0, again.stderr
    assert sorted(p.name for p in tmp_path.glob("progress.db.bak-v*")) == ["progress.db.bak-v15"]


def test_the_backup_can_be_switched_off(tmp_path: Path) -> None:
    db_path = tmp_path / "progress.db"
    _old_db(db_path)

    result = run_cli(
        "status",
        "--db",
        str(db_path),
        home=tmp_path,
        env={"PYIMGTAG_NO_MIGRATION_BACKUP": "1"},
    )

    assert result.returncode == 0, result.stderr
    assert not list(tmp_path.glob("progress.db.bak-v*"))
    assert _user_version(db_path) == SCHEMA_VERSION
