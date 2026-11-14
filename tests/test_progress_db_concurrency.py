"""Concurrency contract of the shared ProgressDB connection.

The dashboard hands one ``ProgressDB`` (one ``sqlite3`` connection) to the
event loop, ``asyncio.to_thread`` workers and job threads at once. These tests
pin that every facade method is serialised on one re-entrant lock, that
iterator methods release it between items, and that the lock also exists on
instances built with ``ProgressDB.__new__`` plus an injected ``_conn``.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest

from pyimgtag.db.progress_db import ProgressDB

_JOIN_TIMEOUT = 10.0


class _GatedConnection:
    """Proxy a real connection, pausing or failing chosen statements per thread."""

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real
        self.merger_paused = threading.Event()
        self.release_merger = threading.Event()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    def __enter__(self) -> sqlite3.Connection:
        return self._real.__enter__()

    def __exit__(self, *exc: object) -> Any:
        return self._real.__exit__(*exc)  # type: ignore[arg-type]

    def execute(self, sql: str, params: Any = ()) -> sqlite3.Cursor:
        thread = threading.current_thread().name
        if thread == "merger" and sql.startswith("DELETE FROM persons"):
            self.merger_paused.set()
            self.release_merger.wait(_JOIN_TIMEOUT)
        if thread == "failer" and sql.startswith("UPDATE processed_images SET tags"):
            raise sqlite3.OperationalError("injected failure")
        return self._real.execute(sql, params)


def _seed(db: ProgressDB) -> None:
    conn = db._conn
    conn.execute("INSERT INTO persons (id, label) VALUES (1, 'source'), (2, 'target')")
    conn.executemany(
        "INSERT INTO faces (image_path, person_id) VALUES (?, ?)",
        [(f"/p/{i}.jpg", 1) for i in range(5)],
    )
    conn.executemany(
        "INSERT INTO processed_images (file_path, tags, status) VALUES (?, ?, 'ok')",
        [(f"/p/{i}.jpg", json.dumps(["beach", "sun"])) for i in range(5)],
    )
    conn.commit()


def _run(name: str, target: Any, errors: list[BaseException]) -> threading.Thread:
    def body() -> None:
        try:
            target()
        except BaseException as exc:  # noqa: BLE001 — surfaced by the test
            errors.append(exc)

    thread = threading.Thread(target=body, name=name, daemon=True)
    thread.start()
    return thread


def test_a_rollback_in_one_thread_cannot_split_another_threads_write(tmp_path: Path) -> None:
    db = ProgressDB(tmp_path / "p.db")
    _seed(db)
    gated = _GatedConnection(db._conn)
    db._conn = gated  # type: ignore[assignment]
    merger_errors: list[BaseException] = []
    failer_errors: list[BaseException] = []

    merger = _run("merger", lambda: db.merge_persons(1, 2), merger_errors)
    assert gated.merger_paused.wait(_JOIN_TIMEOUT), "merge never reached its DELETE"
    failer = _run("failer", lambda: db.rename_tag("beach", "shore"), failer_errors)
    # With the lock the failing writer waits for the merge; without it, it
    # rolls back the merge's uncommitted UPDATE right here.
    failer.join(0.5)
    gated.release_merger.set()
    merger.join(_JOIN_TIMEOUT)
    failer.join(_JOIN_TIMEOUT)

    assert not merger.is_alive() and not failer.is_alive()
    assert merger_errors == []
    assert [type(e) for e in failer_errors] == [sqlite3.OperationalError]
    orphans = db._conn.execute(
        "SELECT COUNT(*) FROM faces WHERE person_id IS NOT NULL"
        " AND person_id NOT IN (SELECT id FROM persons)"
    ).fetchone()[0]
    assert orphans == 0
    assert db._conn.execute("SELECT DISTINCT person_id FROM faces").fetchall() == [(2,)]
    db.close()


def test_concurrent_readers_and_writers_do_not_raise(tmp_path: Path) -> None:
    db = ProgressDB(tmp_path / "p.db")
    _seed(db)
    errors: list[BaseException] = []

    def writer(n: int) -> None:
        for i in range(40):
            db.update_image_tags(f"/p/{(n + i) % 5}.jpg", [f"t{n}", f"i{i}"])

    def reader() -> None:
        for _ in range(40):
            assert len(db.get_images(limit=10)) == 5
            db.get_tag_counts()

    threads = [_run(f"w{n}", lambda n=n: writer(n), errors) for n in range(4)]
    threads += [_run(f"r{n}", reader, errors) for n in range(4)]
    for thread in threads:
        thread.join(_JOIN_TIMEOUT)

    assert not any(t.is_alive() for t in threads)
    assert errors == []
    db.close()


def test_generators_do_not_hold_the_lock_while_suspended(tmp_path: Path) -> None:
    db = ProgressDB(tmp_path / "p.db")
    _seed(db)
    errors: list[BaseException] = []

    paths = db.iter_image_paths(batch_size=1)
    first = next(paths)
    hashes = db.iter_paths_missing_phash()
    next(hashes)
    writer = _run("writer", lambda: db.update_image_tags(first, ["moved"]), errors)
    writer.join(2.0)

    assert not writer.is_alive(), "a suspended iterator blocked another thread's write"
    assert errors == []
    assert db.get_image(first)["tags"] == ["moved"]
    assert [first, *paths] == [f"/p/{i}.jpg" for i in range(5)]
    assert len([*hashes]) == 4
    db.close()


def test_the_lock_exists_on_objects_built_with_new(tmp_path: Path) -> None:
    db = ProgressDB.__new__(ProgressDB)
    db._path = tmp_path / "p.db"
    db._conn = sqlite3.connect(str(db._path), check_same_thread=False)
    db._create_table()

    seen: list[object] = []
    barrier = threading.Barrier(8)

    def grab() -> None:
        barrier.wait()
        seen.append(db._lock)

    threads = [threading.Thread(target=grab) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(_JOIN_TIMEOUT)

    assert len({id(lock) for lock in seen}) == 1
    assert seen[0] is db._lock
    assert db.get_stats() == {"total": 0, "ok": 0, "error": 0}
    db.close()


def test_every_connection_method_waits_for_the_lock(tmp_path: Path) -> None:
    db = ProgressDB(tmp_path / "p.db")
    unlocked = [
        name
        for name, attr in vars(ProgressDB).items()
        if callable(attr)
        and not name.startswith("__")
        and not getattr(attr, "__pyimgtag_locked__", False)
    ]
    assert unlocked == []

    holder_ready = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with db._lock:
            holder_ready.set()
            release.wait(_JOIN_TIMEOUT)

    holder = threading.Thread(target=hold, daemon=True)
    holder.start()
    assert holder_ready.wait(_JOIN_TIMEOUT)
    errors: list[BaseException] = []
    caller = _run("caller", db.get_stats, errors)
    caller.join(0.3)
    assert caller.is_alive(), "get_stats ran while another thread held the lock"
    release.set()
    caller.join(_JOIN_TIMEOUT)
    holder.join(_JOIN_TIMEOUT)
    assert not caller.is_alive() and errors == []
    db.close()


def test_lock_is_reentrant_for_nested_facade_calls(tmp_path: Path) -> None:
    db = ProgressDB(tmp_path / "p.db")
    with db._lock:
        assert db.get_stats()["total"] == 0
    db.close()


def test_connections_wait_for_a_busy_writer(tmp_path: Path) -> None:
    # Pins the wait explicitly: it must not drop if sqlite3's default changes.
    db = ProgressDB(tmp_path / "p.db")
    assert db._conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    db.close()


@pytest.mark.parametrize("method", ["iter_image_paths", "iter_paths_missing_phash"])
def test_iterators_stop_cleanly_on_an_empty_db(tmp_path: Path, method: str) -> None:
    db = ProgressDB(tmp_path / "p.db")
    assert list(getattr(db, method)()) == []
    db.close()


def test_static_and_class_methods_are_refused() -> None:
    from pyimgtag.db.progress_db import _serialise_connection_access

    class WithStatic:
        @staticmethod
        def helper() -> int:
            return 1

    with pytest.raises(TypeError, match="WithStatic.helper"):
        _serialise_connection_access(WithStatic)
