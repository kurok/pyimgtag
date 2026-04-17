"""Tests for the judge subcommand."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _make_args(tmp_path: Path, **overrides) -> MagicMock:
    args = MagicMock()
    args.input_dir = str(tmp_path)
    args.photos_library = None
    args.extensions = "jpg,heic"
    args.limit = None
    args.date = None
    args.date_from = None
    args.date_to = None
    args.min_score = None
    args.sort_by = "score"
    args.verbose = False
    args.output_json = None
    args.ollama_url = "http://localhost:11434"
    args.model = "gemma4:e4b"
    args.max_dim = 512
    args.timeout = 5
    args.no_recursive = False
    for k, v in overrides.items():
        setattr(args, k, v)
    return args


def _make_scores(**overrides):
    from pyimgtag.models import JudgeScores
    defaults = dict(
        impact=4.0, story_subject=4.0, composition_center=4.0,
        lighting=4.0, creativity_style=4.0, color_mood=4.0,
        presentation_crop=4.0, technical_excellence=4.0,
        focus_sharpness=4.0, exposure_tonal=4.0, noise_cleanliness=4.0,
        subject_separation=4.0, edit_integrity=4.0,
        verdict="Good overall.",
    )
    defaults.update(overrides)
    return JudgeScores(**defaults)


class TestCmdJudgeBasic:
    def test_returns_0_on_success(self, tmp_path: Path) -> None:
        from pyimgtag.commands.judge import cmd_judge
        (tmp_path / "photo.jpg").write_bytes(b"x")
        args = _make_args(tmp_path)

        with (
            patch("pyimgtag.commands.judge.check_ollama", return_value=(True, "")),
            patch("pyimgtag.commands.judge.OllamaClient") as mock_cls,
        ):
            mock_client = MagicMock()
            mock_client.judge_image.return_value = _make_scores()
            mock_cls.return_value = mock_client
            rc = cmd_judge(args, MagicMock())

        assert rc == 0

    def test_returns_0_when_no_files(self, tmp_path: Path) -> None:
        from pyimgtag.commands.judge import cmd_judge
        args = _make_args(tmp_path)

        with patch("pyimgtag.commands.judge.check_ollama", return_value=(True, "")):
            rc = cmd_judge(args, MagicMock())

        assert rc == 0

    def test_returns_1_when_ollama_unavailable(self, tmp_path: Path) -> None:
        from pyimgtag.commands.judge import cmd_judge
        args = _make_args(tmp_path)

        with patch("pyimgtag.commands.judge.check_ollama", return_value=(False, "not running")):
            rc = cmd_judge(args, MagicMock())

        assert rc == 1

    def test_judge_image_called_per_file(self, tmp_path: Path) -> None:
        from pyimgtag.commands.judge import cmd_judge
        (tmp_path / "a.jpg").write_bytes(b"x")
        (tmp_path / "b.jpg").write_bytes(b"x")
        args = _make_args(tmp_path)

        with (
            patch("pyimgtag.commands.judge.check_ollama", return_value=(True, "")),
            patch("pyimgtag.commands.judge.OllamaClient") as mock_cls,
        ):
            mock_client = MagicMock()
            mock_client.judge_image.return_value = _make_scores()
            mock_cls.return_value = mock_client
            cmd_judge(args, MagicMock())

        assert mock_client.judge_image.call_count == 2

    def test_min_score_filter(self, tmp_path: Path) -> None:
        from pyimgtag.commands.judge import cmd_judge
        (tmp_path / "photo.jpg").write_bytes(b"x")
        args = _make_args(tmp_path, min_score=4.5)

        with (
            patch("pyimgtag.commands.judge.check_ollama", return_value=(True, "")),
            patch("pyimgtag.commands.judge.OllamaClient") as mock_cls,
        ):
            mock_client = MagicMock()
            mock_client.judge_image.return_value = _make_scores()  # weighted ~4.0, below 4.5
            mock_cls.return_value = mock_client
            rc = cmd_judge(args, MagicMock())

        assert rc == 0  # no crash, just filtered output

    def test_output_json_written(self, tmp_path: Path) -> None:
        import json
        from pyimgtag.commands.judge import cmd_judge
        (tmp_path / "photo.jpg").write_bytes(b"x")
        out = tmp_path / "results.json"
        args = _make_args(tmp_path, output_json=str(out))

        with (
            patch("pyimgtag.commands.judge.check_ollama", return_value=(True, "")),
            patch("pyimgtag.commands.judge.OllamaClient") as mock_cls,
        ):
            mock_client = MagicMock()
            mock_client.judge_image.return_value = _make_scores()
            mock_cls.return_value = mock_client
            cmd_judge(args, MagicMock())

        assert out.exists()
        data = json.loads(out.read_text())
        assert isinstance(data, list)
        assert data[0]["file_name"] == "photo.jpg"
        assert "weighted_score" in data[0]
        assert "scores" in data[0]
