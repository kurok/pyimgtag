"""Tests for ``pyimgtag watch``: lock, stability gate, incremental loop, service dispatch."""

from __future__ import annotations

import os
import subprocess  # nosec B404
import sys
import textwrap
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pyimgtag.commands.watch import WatchLock, WatchLockHeld, _Watcher, lock_path_for_db
from pyimgtag.main import build_parser, main
from pyimgtag.models import ImageResult, TagResult
from pyimgtag.progress_db import ProgressDB
from pyimgtag.run_session import RunSession


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("PYIMGTAG_NO_UPDATE_CHECK", "1")
    monkeypatch.setenv("PYIMGTAG_NO_WEB", "1")
    monkeypatch.delenv("PYIMGTAG_VOCABULARY", raising=False)
    monkeypatch.delenv("PYIMGTAG_PROMPT_TEMPLATE", raising=False)


# --- lock ----------------------------------------------------------------------------------


def test_lock_path_for_db(tmp_path):
    assert lock_path_for_db(tmp_path / "p.db") == tmp_path / "p.db.watch.lock"
    default = lock_path_for_db(None)
    assert default.name == "progress.db.watch.lock" and ".cache" in default.parts


def test_lock_acquire_release_and_contention(tmp_path):
    path = tmp_path / "x.lock"
    with WatchLock(path):
        assert path.read_text() == str(os.getpid())
        # A *different* live pid (our parent) holds it → refused, pid in the message.
        path.write_text(str(os.getppid()))
        with pytest.raises(WatchLockHeld, match=str(os.getppid())):
            WatchLock(path).acquire()
        path.write_text(str(os.getpid()))
    assert not path.exists()


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows recycles PIDs fast enough that the dead pid above can already "
    "belong to a new live process, making _pid_alive() flaky",
)
def test_lock_reclaims_stale_and_garbage(tmp_path):
    path = tmp_path / "x.lock"
    proc = subprocess.Popen([sys.executable, "-c", "pass"])  # nosec B603
    proc.wait()
    path.write_text(str(proc.pid))  # dead pid
    with WatchLock(path):
        assert path.read_text() == str(os.getpid())
    path.write_text("not-a-pid")
    with WatchLock(path):
        assert path.read_text() == str(os.getpid())
    assert not path.exists()


def test_lock_release_does_not_remove_foreign_lock(tmp_path):
    path = tmp_path / "x.lock"
    lock = WatchLock(path)
    lock.acquire()
    path.write_text("424242")  # someone else took it over
    lock.release()
    assert path.exists()


# --- stability gate / candidates -------------------------------------------------------


def _watcher(tmp_path: Path, db: Path) -> _Watcher:
    args = build_parser().parse_args(["watch", "--input-dir", str(tmp_path), "--db", str(db)])
    return _Watcher(args, RunSession(command="watch"), MagicMock(), None, {"jpg"})


def test_candidates_stability_gate_and_change_detection(tmp_path):
    db = tmp_path / "p.db"
    ProgressDB(db_path=db).close()
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"x")
    w = _watcher(tmp_path, db)

    todo, pending = w._candidates([photo])
    assert todo == [] and pending == 1  # first sighting: not yet stable

    photo.write_bytes(b"xx")  # still growing
    todo, pending = w._candidates([photo])
    assert todo == [] and pending == 1

    todo, pending = w._candidates([photo])
    assert todo == [photo] and pending == 0  # stable now

    with ProgressDB(db_path=db) as pdb:
        pdb.mark_done(photo, ImageResult(file_path=str(photo), tags=["t"]))
    todo, _ = w._candidates([photo])
    assert todo == []  # complete in DB → never re-hit

    photo.write_bytes(b"changed!")
    os.utime(photo, (time.time() + 5, time.time() + 5))
    w._candidates([photo])  # pending
    todo, _ = w._candidates([photo])
    assert todo == [photo]  # changed size/mtime → re-processed


def test_candidates_skips_failed_until_changed(tmp_path):
    db = tmp_path / "p.db"
    ProgressDB(db_path=db).close()
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"x")
    w = _watcher(tmp_path, db)
    w._candidates([photo])
    todo, _ = w._candidates([photo])
    assert todo == [photo]
    w.failed[str(photo)] = w.previous[str(photo)]
    assert w._candidates([photo])[0] == []
    photo.write_bytes(b"xy")
    w._candidates([photo])
    assert w._candidates([photo])[0] == [photo]  # failure memory dropped on change
    assert str(photo) not in w.failed


# --- end-to-end loop ---------------------------------------------------------------------


def _run_watch(argv: list[str], tag_image, ollama_url_ok: bool = True) -> int:
    with (
        patch("pyimgtag.commands.run.check_ollama", return_value=(ollama_url_ok, "")),
        patch("pyimgtag.commands.run.OllamaClient") as cls,
    ):
        client = MagicMock()
        client.tag_image.side_effect = tag_image
        client.prompt_builder = None
        cls.return_value = client
        return main(["watch", *argv])


def test_watch_tags_new_files_and_never_rehits_unchanged(tmp_path, capsys):
    photos = tmp_path / "photos"
    photos.mkdir()
    (photos / "a.jpg").write_bytes(b"a")
    db = tmp_path / "p.db"
    calls: list[str] = []

    def tag_image(path, context=None):
        calls.append(Path(path).name)
        if Path(path).name == "a.jpg":
            (photos / "b.jpg").write_bytes(b"bb")  # "added after startup"
        return TagResult(tags=["t"], summary="s")

    rc = _run_watch(
        [
            "--input-dir",
            str(photos),
            "--db",
            str(db),
            "--interval",
            "0.01",
            "--max-cycles",
            "5",
            "--no-web",
        ],
        tag_image,
    )
    assert rc == 0
    # cycle1: a pending; cycle2: a tagged (+ b appears); cycle3: b pending;
    # cycle4: b tagged; cycle5: nothing. Unchanged files never re-hit the model.
    assert calls == ["a.jpg", "b.jpg"]
    with ProgressDB(db_path=db) as pdb:
        names = sorted(Path(r["file_path"]).name for r in pdb.query_images())
    assert names == ["a.jpg", "b.jpg"]
    err = capsys.readouterr().err
    assert "[watch] cycle 1: checked 1 · pending 1 · new 0 · tagged 0 · errors 0" in err
    assert "[watch] cycle 2: checked 1 · pending 0 · new 1 · tagged 1 · errors 0" in err
    assert "[watch] cycle 4: checked 2 · pending 0 · new 1 · tagged 1 · errors 0" in err
    assert "[watch] cycle 5: checked 2 · pending 0 · new 0 · tagged 0 · errors 0" in err
    assert not lock_path_for_db(db).exists()


def test_watch_failed_file_not_retried_until_changed(tmp_path, capsys):
    photos = tmp_path / "photos"
    photos.mkdir()
    (photos / "a.jpg").write_bytes(b"a")
    db = tmp_path / "p.db"
    calls: list[str] = []

    def tag_image(path, context=None):
        calls.append(Path(path).name)
        return TagResult(error="boom")

    rc = _run_watch(
        ["--input-dir", str(photos), "--db", str(db), "--interval", "0.01", "--max-cycles", "4"],
        tag_image,
    )
    assert rc == 0
    assert calls == ["a.jpg"]
    assert "errors 1" in capsys.readouterr().err


def test_watch_second_instance_exits_with_holder_pid(tmp_path, capsys):
    photos = tmp_path / "photos"
    photos.mkdir()
    db = tmp_path / "p.db"
    # Simulate a live holder: our parent process (pytest / xdist worker parent).
    lock_path = lock_path_for_db(db)
    lock_path.write_text(str(os.getppid()))
    try:
        rc = _run_watch(["--input-dir", str(photos), "--db", str(db), "--max-cycles", "1"], None)
    finally:
        lock_path.unlink(missing_ok=True)
    assert rc == 1
    err = capsys.readouterr().err
    assert f"pid {os.getppid()}" in err and "already using this DB" in err


def test_watch_dashboard_stop_request_ends_loop(tmp_path):
    """The dashboard Stop button (session.request_stop) ends watch after the cycle."""
    photos = tmp_path / "photos"
    photos.mkdir()
    (photos / "a.jpg").write_bytes(b"a")
    db = tmp_path / "p.db"
    started: list[RunSession] = []
    real_bootstrap = "pyimgtag.webapp.bootstrap.start_dashboard_for"

    def fake_bootstrap(args, command):
        s = RunSession(command=command)
        started.append(s)
        return s, None

    def tag_image(path, context=None):
        started[0].request_stop()  # press Stop while the first image is in flight
        return TagResult(tags=["t"])

    with patch(real_bootstrap, side_effect=fake_bootstrap):
        rc = _run_watch(
            ["--input-dir", str(photos), "--db", str(db), "--interval", "30", "--max-cycles", "0"],
            tag_image,
        )
    assert rc == 0
    assert started[0].snapshot()["state"] == "interrupted"
    with ProgressDB(db_path=db) as pdb:
        assert len(pdb.query_images()) == 1  # in-flight image was committed


@pytest.mark.parametrize(
    "argv",
    [
        ["watch", "--input-dir", "x", "--dry-run"],
        ["watch", "--input-dir", "x", "--no-cache"],
        ["watch", "--input-dir", "x", "--interval", "0"],
        ["watch", "--input-dir", "x", "--max-cycles", "-1"],
        ["watch", "--install-service"],
        ["watch"],
    ],
)
def test_watch_arg_validation(argv, tmp_path):
    with pytest.raises(SystemExit):
        main(argv)


def test_watch_accepts_run_flags():
    args = build_parser().parse_args(
        [
            "watch",
            "--input-dir",
            "x",
            "--backend",
            "openai",
            "--write-exif",
            "--dedup",
            "--interval",
            "7",
        ]
    )
    assert args.backend == "openai" and args.write_exif and args.dedup and args.interval == 7.0
    assert args.max_cycles == 0 and not args.install_service


def test_watch_install_uninstall_dispatch(tmp_path):
    with patch("pyimgtag.service_units.install_service", return_value=0) as inst:
        assert main(["watch", "--input-dir", str(tmp_path), "--install-service", "--force"]) == 0
    inst.assert_called_once_with(
        ["watch", "--input-dir", str(tmp_path), "--install-service", "--force"], force=True
    )
    with patch("pyimgtag.service_units.uninstall_service", return_value=0) as un:
        assert main(["watch", "--uninstall-service"]) == 0
    un.assert_called_once_with()


def test_watch_invalid_vocabulary_is_startup_error(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    rc = _run_watch(
        ["--input-dir", str(tmp_path), "--db", str(tmp_path / "p.db"), "--vocabulary", str(bad)],
        None,
    )
    assert rc == 1 and "bad.json" in capsys.readouterr().err
    assert not lock_path_for_db(tmp_path / "p.db").exists()


def test_dashboard_labels_watch_as_watching():
    from pyimgtag.webapp.dashboard_server import _render_html

    html = _render_html()
    assert "'watching'" in html and "d.command === 'watch'" in html


# --- signals (child process) -------------------------------------------------------------

_CHILD = textwrap.dedent(
    """
    import sys, time
    from pathlib import Path
    from unittest.mock import MagicMock, patch
    from pyimgtag.main import main
    from pyimgtag.models import TagResult

    photos, db = sys.argv[1], sys.argv[2]

    def tag_image(path, context=None):
        print("INFLIGHT", flush=True)
        time.sleep(1.5)          # SIGTERM arrives while this image is in flight
        return TagResult(tags=["t"], summary="s")

    with patch("pyimgtag.commands.run.check_ollama", return_value=(True, "")), \\
         patch("pyimgtag.commands.run.OllamaClient") as cls:
        client = MagicMock()
        client.tag_image.side_effect = tag_image
        client.prompt_builder = None
        cls.return_value = client
        argv = ["watch", "--input-dir", photos, "--db", db, "--interval", "0.05", "--no-web"]
        sys.exit(main(argv))
    """
)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal semantics")
def test_watch_sigterm_finishes_inflight_and_releases_lock(tmp_path):
    photos = tmp_path / "photos"
    photos.mkdir()
    (photos / "a.jpg").write_bytes(b"a")
    db = tmp_path / "p.db"
    env = dict(os.environ, PYIMGTAG_NO_UPDATE_CHECK="1", PYIMGTAG_NO_WEB="1")
    proc = subprocess.Popen(  # nosec B603
        [sys.executable, "-c", _CHILD, str(photos), str(db)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    assert proc.stdout is not None
    line = proc.stdout.readline()
    assert line.strip() == "INFLIGHT"
    import signal

    proc.send_signal(signal.SIGTERM)
    _out, err = proc.communicate(timeout=30)
    assert proc.returncode == 0, err
    assert "finishing the in-flight image" in err
    assert "[watch] stopped" in err
    with ProgressDB(db_path=db) as pdb:
        assert [Path(r["file_path"]).name for r in pdb.query_images()] == ["a.jpg"]
    assert not lock_path_for_db(db).exists()
