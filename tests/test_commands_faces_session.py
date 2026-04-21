"""Tests for faces scan + RunSession integration."""

from __future__ import annotations

import threading
import time
from unittest.mock import patch


def test_faces_scan_no_web_does_not_register_session(tmp_path):
    """--no-web on faces scan leaves the registry empty."""
    from pyimgtag import run_registry
    from pyimgtag.commands.faces import cmd_faces
    from pyimgtag.main import build_parser

    run_registry.set_current(None)

    img = tmp_path / "a.jpg"
    img.write_bytes(b"x")

    parser = build_parser()
    args = parser.parse_args(
        [
            "faces",
            "scan",
            "--input-dir",
            str(tmp_path),
            "--extensions",
            "jpg",
            "--no-web",
        ]
    )

    with (
        patch("pyimgtag.commands.faces.scan_and_store", return_value=0),
        patch("pyimgtag.face_detection._check_face_recognition"),
    ):
        rc = cmd_faces(args)

    assert rc == 0
    assert run_registry.get_current() is None


def test_faces_scan_pause_gate_blocks_between_files(tmp_path):
    """Pause must stop faces scan between files; resume must continue."""
    from pyimgtag import run_registry
    from pyimgtag.commands.faces import cmd_faces
    from pyimgtag.main import build_parser
    from pyimgtag.run_session import RunSession

    run_registry.set_current(None)

    for i in range(3):
        (tmp_path / f"{i}.jpg").write_bytes(b"x")

    parser = build_parser()
    args = parser.parse_args(
        [
            "faces",
            "scan",
            "--input-dir",
            str(tmp_path),
            "--extensions",
            "jpg",
            "--no-web",
        ]
    )

    session = RunSession(command="faces scan")
    run_registry.set_current(session)

    scanned_paths: list[str] = []
    pause_after_first = threading.Event()
    pause_requested = threading.Event()

    def fake_scan(path, *a, **kw):
        scanned_paths.append(str(path))
        if len(scanned_paths) == 1:
            pause_after_first.set()
            pause_requested.wait(timeout=5.0)
        return 0  # no faces

    result_holder: dict = {}

    def run_cmd():
        result_holder["rc"] = cmd_faces(args)

    with (
        patch("pyimgtag.commands.faces.scan_and_store", side_effect=fake_scan),
        patch("pyimgtag.face_detection._check_face_recognition"),
        patch("pyimgtag.commands.faces.start_dashboard_for", return_value=(session, None)),
    ):
        worker = threading.Thread(target=run_cmd, daemon=True)
        worker.start()

        assert pause_after_first.wait(timeout=3.0)
        session.request_pause()
        pause_requested.set()

        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            if session.snapshot()["state"] == "paused":
                break
            time.sleep(0.02)
        assert session.snapshot()["state"] == "paused"
        assert len(scanned_paths) == 1

        session.resume()
        worker.join(timeout=5.0)

    assert result_holder["rc"] == 0
    assert len(scanned_paths) == 3
    snap = session.snapshot()
    assert snap["state"] == "completed"
    assert snap["recent"], "expected at least one scanned event"
    run_registry.set_current(None)
