"""End to end: the real CLI rejects bad values before any work (#393).

Runs ``python -m pyimgtag`` in a child process (see ``tests/e2e_support.py``).
Every case fails in argparse, so no Ollama, scan or database is touched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e_support import run_cli


@pytest.mark.parametrize(
    ("args", "flag", "message"),
    [
        (["--date", "2026-13-45"], "--date", "expected YYYY-MM-DD, got '2026-13-45'"),
        (["--limit", "-5"], "--limit", "expected a whole number of 0 or more, got '-5'"),
        (["--jobs", "-3"], "--jobs/-j", "expected a whole number of 0 or more, got '-3'"),
        (["--timeout", "0"], "--timeout", "expected a whole number of 1 or more, got '0'"),
    ],
)
def test_run_rejects_a_bad_value_without_a_traceback(
    tmp_path: Path, args: list[str], flag: str, message: str
) -> None:
    photos = tmp_path / "photos"
    photos.mkdir()
    (photos / "a.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    db = tmp_path / "x.db"

    result = run_cli(
        "run", "--input-dir", str(photos), "--db", str(db), "--no-web", *args, home=tmp_path
    )

    assert result.returncode == 2
    assert f"argument {flag}: {message}" in result.stderr
    assert "Traceback" not in result.stderr
    assert not db.exists()


def test_query_rejects_a_negative_limit(tmp_path: Path) -> None:
    result = run_cli("query", "--limit", "-1", home=tmp_path)
    assert result.returncode == 2
    assert "argument --limit" in result.stderr
