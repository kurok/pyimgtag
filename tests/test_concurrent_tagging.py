"""Integration tests for ``run -j`` / ``judge -j`` (issue #327).

Everything here is network-free: the vision client is a fake with an
artificial latency, ``read_exif`` is stubbed where it would only add noise,
and all state lives under ``tmp_path``.
"""

from __future__ import annotations

import csv
import json
import os
import signal
import subprocess  # nosec B404
import sys
import textwrap
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pyimgtag.main import build_parser, main
from pyimgtag.models import ExifData, JudgeScores, TagResult
from pyimgtag.progress_db import ProgressDB


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("PYIMGTAG_NO_UPDATE_CHECK", "1")
    monkeypatch.setenv("PYIMGTAG_NO_WEB", "1")
    monkeypatch.delenv("PYIMGTAG_VOCABULARY", raising=False)
    monkeypatch.delenv("PYIMGTAG_PROMPT_TEMPLATE", raising=False)
    monkeypatch.delenv("PYIMGTAG_BACKEND", raising=False)


def _make_images(tmp_path: Path, count: int) -> Path:
    photos = tmp_path / "photos"
    photos.mkdir()
    for i in range(count):
        (photos / f"img{i:03d}.jpg").write_bytes(b"x" * (i + 1))
    return photos


@contextmanager
def _fake_run_client(tag_image, *, stub_exif: bool = True):
    """Patch ``run``'s Ollama client with a fake; yield the mock client."""
    client = MagicMock()
    client.tag_image.side_effect = tag_image
    client.prompt_builder = None
    patches = [
        patch("pyimgtag.commands.run.check_ollama", return_value=(True, "")),
        patch("pyimgtag.commands.run.OllamaClient", return_value=client),
    ]
    if stub_exif:
        patches.append(patch("pyimgtag.commands.run.read_exif", return_value=ExifData()))
    for p in patches:
        p.start()
    try:
        yield client
    finally:
        for p in reversed(patches):
            p.stop()


def _tagger(latency: float = 0.0):
    def tag_image(path, context=None):
        if latency:
            time.sleep(latency)
        return TagResult(tags=[Path(path).stem], summary=f"summary of {Path(path).name}")

    return tag_image


# --- speedup -----------------------------------------------------------------------------


@pytest.mark.slow
def test_jobs_4_is_at_least_3x_faster_than_serial(tmp_path):
    photos = _make_images(tmp_path, 24)
    latency = 0.2

    def _timed(jobs: int) -> float:
        argv = [
            "run",
            "--input-dir",
            str(photos),
            "--no-cache",
            "--no-web",
            "--jobs",
            str(jobs),
        ]
        with _fake_run_client(_tagger(latency)):
            start = time.monotonic()
            assert main(argv) == 0
            return time.monotonic() - start

    serial = _timed(1)
    parallel = _timed(4)
    # Compare rates, not absolute budgets: shared CI runners are slow, but the
    # ratio between the two paths on the same machine is stable.
    assert serial / parallel >= 3.0, f"serial={serial:.2f}s parallel={parallel:.2f}s"


# --- -j 1 regression ---------------------------------------------------------------------


def test_jobs_1_output_matches_and_never_uses_the_pipeline(tmp_path):
    photos = _make_images(tmp_path, 6)
    serial_json = tmp_path / "serial.json"
    parallel_json = tmp_path / "parallel.json"

    def _run(jobs: int, out: Path) -> None:
        argv = [
            "run",
            "--input-dir",
            str(photos),
            "--no-cache",
            "--no-web",
            "--output-json",
            str(out),
            "--jobs",
            str(jobs),
        ]
        with _fake_run_client(_tagger()):
            assert main(argv) == 0

    with patch("pyimgtag.commands.run.run_pipeline") as pipeline:
        _run(1, serial_json)
    pipeline.assert_not_called()

    _run(4, parallel_json)
    assert json.loads(serial_json.read_text()) == json.loads(parallel_json.read_text())


def test_jobs_1_uses_process_one_serially(tmp_path):
    photos = _make_images(tmp_path, 3)
    from pyimgtag.commands import run as run_mod

    seen: list[str] = []
    real = run_mod._process_one

    def spy(*a, **kw):
        seen.append(threading.current_thread().name)
        return real(*a, **kw)

    argv = ["run", "--input-dir", str(photos), "--no-cache", "--no-web"]
    with _fake_run_client(_tagger()), patch.object(run_mod, "_process_one", spy):
        assert main(argv) == 0
    assert len(seen) == 3
    assert set(seen) == {"MainThread"}


# --- ordering ----------------------------------------------------------------------------


def test_outputs_stay_in_scan_order_with_jittered_latency(tmp_path, capsys):
    photos = _make_images(tmp_path, 12)
    names = sorted(p.name for p in photos.iterdir())
    out_json = tmp_path / "out.json"
    out_csv = tmp_path / "out.csv"
    db = tmp_path / "p.db"

    def tag_image(path, context=None):
        # Reverse-ordered sleeps: completion order is the inverse of scan order.
        idx = int(Path(path).stem.removeprefix("img"))
        time.sleep((12 - idx) * 0.01)
        return TagResult(tags=["t"], summary="s")

    argv = [
        "run",
        "--input-dir",
        str(photos),
        "--db",
        str(db),
        "--no-web",
        "--jsonl-stdout",
        "--output-json",
        str(out_json),
        "--output-csv",
        str(out_csv),
        "--jobs",
        "4",
    ]
    with _fake_run_client(tag_image):
        assert main(argv) == 0

    assert [r["file_name"] for r in json.loads(out_json.read_text())] == names
    with open(out_csv, newline="", encoding="utf-8") as fh:
        assert [row["file_name"] for row in csv.DictReader(fh)] == names
    stdout_lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("{")]
    assert [json.loads(ln)["file_name"] for ln in stdout_lines] == names
    with ProgressDB(db_path=db) as pdb:
        assert sorted(Path(r["file_path"]).name for r in pdb.query_images()) == names


def test_judge_jobs_4_prints_and_saves_in_scan_order(tmp_path, capsys):
    photos = _make_images(tmp_path, 10)
    names = sorted(p.name for p in photos.iterdir())
    db = tmp_path / "p.db"
    out = tmp_path / "judge.json"

    def judge_image(path):
        idx = int(Path(path).stem.removeprefix("img"))
        time.sleep((10 - idx) * 0.01)
        return JudgeScores(score=7, verdict="keep", reason="r")

    client = MagicMock()
    client.judge_image.side_effect = judge_image
    with (
        patch("pyimgtag.commands.judge.check_ollama", return_value=(True, "")),
        patch("pyimgtag.commands.judge.OllamaClient", return_value=client),
    ):
        argv = [
            "judge",
            "--input-dir",
            str(photos),
            "--db",
            str(db),
            "--no-web",
            "--sort-by",
            "name",
            "--output-json",
            str(out),
            "--jobs",
            "4",
        ]
        assert main(argv) == 0

    printed = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("[")]
    assert [ln.split()[1] for ln in printed] == names
    assert [f"[{i}/10]" for i in range(1, 11)] == [ln.split()[0] for ln in printed]
    assert [r["file_name"] for r in json.loads(out.read_text())] == names
    with ProgressDB(db_path=db) as pdb:
        assert len(pdb.get_all_judge_results()) == 10


# --- SQLite single-writer stress ----------------------------------------------------------


def test_sqlite_writes_all_come_from_the_main_thread_under_j8(tmp_path):
    photos = _make_images(tmp_path, 200)
    db = tmp_path / "p.db"
    writer_threads: set[str] = set()
    real_mark_done = ProgressDB.mark_done

    def spy_mark_done(self, file_path, result):
        writer_threads.add(threading.current_thread().name)
        return real_mark_done(self, file_path, result)

    argv = ["run", "--input-dir", str(photos), "--db", str(db), "--no-web", "--jobs", "8"]
    with (
        _fake_run_client(_tagger()),
        patch.object(ProgressDB, "mark_done", spy_mark_done),
    ):
        assert main(argv) == 0

    assert writer_threads == {"MainThread"}
    with ProgressDB(db_path=db) as pdb:
        assert len(pdb.query_images()) == 200


# --- pause gate ---------------------------------------------------------------------------


def test_pause_gate_stops_new_submissions_until_resume(tmp_path):
    from pyimgtag.commands import run as run_mod
    from pyimgtag.run_session import RunSession

    photos = _make_images(tmp_path, 30)
    session = RunSession(command="run")
    session.mark_running()
    args = build_parser().parse_args(
        ["run", "--input-dir", str(photos), "--no-cache", "--no-web", "--jobs", "2"]
    )
    args._vocabulary = None

    calls: list[str] = []
    lock = threading.Lock()
    first = threading.Event()

    client = MagicMock()

    def tag_image(path, context=None):
        with lock:
            calls.append(path)
        first.set()
        time.sleep(0.02)
        return TagResult(tags=["t"], summary="s")

    client.tag_image.side_effect = tag_image
    geocoder = MagicMock()
    stats = run_mod._new_stats(len(list(photos.iterdir())))
    results: list = []
    done = threading.Event()

    def runner():
        with patch("pyimgtag.commands.run.read_exif", return_value=ExifData()):
            run_mod._run_tagging(
                sorted(photos.iterdir()),
                "directory",
                args,
                client,
                geocoder,
                None,
                {},
                set(),
                results,
                stats,
                session,
                False,
            )
        done.set()

    thread = threading.Thread(target=runner)
    thread.start()
    try:
        assert first.wait(timeout=5)
        session.request_pause()
        time.sleep(0.3)
        with lock:
            frozen = len(calls)
        time.sleep(0.3)
        with lock:
            assert len(calls) == frozen, "new work was submitted while paused"
        assert frozen < 30
        session.resume()
        assert done.wait(timeout=20)
        assert len(results) == 30
    finally:
        session.resume()
        thread.join(timeout=20)


# --- interrupt (child process) --------------------------------------------------------------

_CHILD = textwrap.dedent(
    """
    import sys, time
    from unittest.mock import MagicMock, patch
    from pyimgtag.main import main
    from pyimgtag.models import ExifData, TagResult

    photos, db = sys.argv[1], sys.argv[2]
    seen = 0

    def tag_image(path, context=None):
        global seen
        seen += 1
        if seen >= 4:
            print("INFLIGHT", flush=True)
        time.sleep(0.6)
        return TagResult(tags=["t"], summary="s")

    client = MagicMock()
    client.tag_image.side_effect = tag_image
    client.prompt_builder = None
    with patch("pyimgtag.commands.run.check_ollama", return_value=(True, "")), \\
         patch("pyimgtag.commands.run.read_exif", return_value=ExifData()), \\
         patch("pyimgtag.commands.run.OllamaClient", return_value=client):
        sys.exit(main([
            "run", "--input-dir", photos, "--db", db, "--no-web",
            "--jobs", "4", "--skip-existing",
        ]))
    """
)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal semantics")
def test_sigint_commits_completed_images_and_resume_does_the_rest(tmp_path):
    photos = _make_images(tmp_path, 24)
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
    assert proc.stdout.readline().strip() == "INFLIGHT"
    time.sleep(0.2)
    proc.send_signal(signal.SIGINT)
    _out, err = proc.communicate(timeout=60)
    assert proc.returncode == 1, err  # interrupted runs exit 1
    assert "Interrupted." in err

    with ProgressDB(db_path=db) as pdb:
        first_pass = {Path(r["file_path"]).name for r in pdb.query_images()}
    assert first_pass, "no completed image was committed"
    assert len(first_pass) < 24, "the run was not interrupted early enough"

    # Second pass with --skip-existing must send only the remainder to the model.
    with _fake_run_client(_tagger()) as client:
        argv = [
            "run",
            "--input-dir",
            str(photos),
            "--db",
            str(db),
            "--no-web",
            "--jobs",
            "4",
            "--skip-existing",
        ]
        assert main(argv) == 0
    assert client.tag_image.call_count == 24 - len(first_pass)
    with ProgressDB(db_path=db) as pdb:
        assert len({Path(r["file_path"]).name for r in pdb.query_images()}) == 24


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal semantics")
def test_resume_after_interrupt_only_calls_the_model_for_the_remainder(tmp_path):
    photos = _make_images(tmp_path, 8)
    db = tmp_path / "p.db"
    # Pre-seed 5 of the 8 as complete.
    done = sorted(photos.iterdir())[:5]
    with ProgressDB(db_path=db) as pdb:
        from pyimgtag.models import ImageResult

        for fp in done:
            pdb.mark_done(fp, ImageResult(file_path=str(fp), file_name=fp.name, tags=["t"]))

    with _fake_run_client(_tagger()) as client:
        argv = [
            "run",
            "--input-dir",
            str(photos),
            "--db",
            str(db),
            "--no-web",
            "--jobs",
            "4",
            "--skip-existing",
        ]
        assert main(argv) == 0
    assert client.tag_image.call_count == 3


# --- parser ---------------------------------------------------------------------------------


class TestConcurrencyFlags:
    def test_run_and_judge_accept_jobs_and_max_rps(self, tmp_path):
        parser = build_parser()
        run_args = parser.parse_args(
            ["run", "--input-dir", str(tmp_path), "-j", "6", "--max-rps", "2.5"]
        )
        assert run_args.jobs == 6 and run_args.max_rps == 2.5
        judge_args = parser.parse_args(
            ["judge", "--input-dir", str(tmp_path), "--jobs", "3", "--max-rps", "0.5"]
        )
        assert judge_args.jobs == 3 and judge_args.max_rps == 0.5

    def test_defaults_are_serial_and_unthrottled(self, tmp_path):
        parser = build_parser()
        for sub in ("run", "judge", "watch"):
            args = parser.parse_args([sub, "--input-dir", str(tmp_path)])
            assert args.jobs == 1, sub
            assert args.max_rps is None, sub

    def test_jobs_zero_auto_resolution(self, tmp_path):
        from pyimgtag.concurrent_pipeline import AUTO_JOBS_CLOUD_CAP, resolve_jobs

        args = build_parser().parse_args(["run", "--input-dir", str(tmp_path), "-j", "0"])
        assert args.jobs == 0
        assert resolve_jobs(args.jobs, "ollama") == 2
        assert 1 <= resolve_jobs(args.jobs, "anthropic") <= AUTO_JOBS_CLOUD_CAP

    def test_max_rps_is_installed_process_wide(self, tmp_path):
        from pyimgtag import concurrent_pipeline

        photos = _make_images(tmp_path, 2)
        argv = [
            "run",
            "--input-dir",
            str(photos),
            "--no-cache",
            "--no-web",
            "--max-rps",
            "100",
        ]
        with _fake_run_client(_tagger()):
            assert main(argv) == 0
        assert concurrent_pipeline.get_global_rate_limit() is not None
