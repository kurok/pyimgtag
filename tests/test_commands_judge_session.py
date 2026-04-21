"""Tests for judge + RunSession integration."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch


def test_judge_no_web_does_not_register_session(tmp_path):
    """--no-web leaves the RunRegistry empty."""
    from pyimgtag import run_registry
    from pyimgtag.commands.judge import cmd_judge
    from pyimgtag.main import build_parser

    run_registry.set_current(None)

    img = tmp_path / "a.jpg"
    img.write_bytes(b"x")

    parser = build_parser()
    args = parser.parse_args(
        ["judge", "--input-dir", str(tmp_path), "--extensions", "jpg", "--no-web"]
    )

    with (
        patch("pyimgtag.commands.judge.OllamaClient") as ollama_cls,
        patch("pyimgtag.commands.judge.check_ollama", return_value=(True, "ok")),
    ):
        ollama = MagicMock()
        ollama.judge_image.return_value = None  # skip scoring — minimal path
        ollama_cls.return_value = ollama
        rc = cmd_judge(args, None)

    assert rc == 0
    assert run_registry.get_current() is None


def test_judge_pause_gate_blocks_between_files(tmp_path):
    """Pause must stop processing before the next judge call; resume must continue."""
    from pyimgtag import run_registry
    from pyimgtag.commands.judge import cmd_judge
    from pyimgtag.main import build_parser
    from pyimgtag.models import JudgeScores
    from pyimgtag.run_session import RunSession

    run_registry.set_current(None)

    for i in range(3):
        (tmp_path / f"{i}.jpg").write_bytes(b"x")

    parser = build_parser()
    args = parser.parse_args(
        ["judge", "--input-dir", str(tmp_path), "--extensions", "jpg", "--no-web"]
    )

    session = RunSession(command="judge")
    run_registry.set_current(session)

    judged_paths: list[str] = []
    pause_after_first = threading.Event()
    pause_requested = threading.Event()

    def fake_judge(path, *a, **kw):
        judged_paths.append(path)
        if len(judged_paths) == 1:
            pause_after_first.set()
            pause_requested.wait(timeout=5.0)
        return JudgeScores(
            impact=3.0,
            story_subject=3.0,
            composition_center=3.0,
            lighting=3.0,
            creativity_style=3.0,
            color_mood=3.0,
            presentation_crop=3.0,
            technical_excellence=3.0,
            focus_sharpness=3.0,
            exposure_tonal=3.0,
            noise_cleanliness=3.0,
            subject_separation=3.0,
            edit_integrity=3.0,
        )

    result_holder: dict = {}

    def run_cmd():
        result_holder["rc"] = cmd_judge(args, None)

    with (
        patch("pyimgtag.commands.judge.OllamaClient") as ollama_cls,
        patch("pyimgtag.commands.judge.check_ollama", return_value=(True, "ok")),
        patch("pyimgtag.commands.judge.start_dashboard_for", return_value=(session, None)),
    ):
        ollama = MagicMock()
        ollama.judge_image.side_effect = fake_judge
        ollama_cls.return_value = ollama

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
        assert len(judged_paths) == 1

        session.resume()
        worker.join(timeout=5.0)

    assert result_holder["rc"] == 0
    assert len(judged_paths) == 3
    snap = session.snapshot()
    assert snap["state"] == "completed"
    assert snap["recent"], "expected at least one recorded judge event"
    run_registry.set_current(None)
