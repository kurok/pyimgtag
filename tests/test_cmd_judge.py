"""Tests for the judge subcommand handler."""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

import pytest

from pyimgtag.commands.judge import cmd_judge
from pyimgtag.models import JudgeScores


def _make_args(**kwargs) -> argparse.Namespace:
    defaults = dict(
        ollama_url="http://localhost:11434",
        model="gemma4:e4b",
        extensions="jpg,jpeg",
        input_dir=None,
        photos_library=None,
        limit=None,
        min_score=None,
        sort_by="score",
        output_json=None,
        verbose=False,
        max_dim=1280,
        timeout=120,
        write_back=False,
        write_back_mode="overwrite",
        no_recursive=False,
        web=False,
        no_web=True,
        web_host="127.0.0.1",
        web_port=8770,
        no_browser=True,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _make_scores() -> JudgeScores:
    return JudgeScores(
        impact=4.0,
        story_subject=3.5,
        composition_center=4.0,
        lighting=3.5,
        creativity_style=3.0,
        color_mood=4.0,
        presentation_crop=3.5,
        technical_excellence=4.0,
        focus_sharpness=4.5,
        exposure_tonal=3.5,
        noise_cleanliness=4.0,
        subject_separation=3.0,
        edit_integrity=3.5,
        verdict="Good shot",
    )


class TestCmdJudgeDBStorage:
    def test_saves_result_to_db(self, tmp_path):
        img = tmp_path / "test.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 10)

        mock_db = MagicMock()
        scores = _make_scores()

        with (
            patch("pyimgtag.commands.judge.check_ollama", return_value=(True, "")),
            patch("pyimgtag.commands.judge.scan_directory", return_value=[img]),
            patch("pyimgtag.commands.judge.OllamaClient") as MockClient,
        ):
            MockClient.return_value.judge_image.return_value = scores
            result = cmd_judge(_make_args(input_dir=str(tmp_path)), mock_db)

        assert result == 0
        mock_db.save_judge_result.assert_called_once()
        saved = mock_db.save_judge_result.call_args[0][0]
        assert saved.file_name == "test.jpg"
        assert saved.scores.impact == pytest.approx(4.0)

    def test_no_db_does_not_crash(self, tmp_path):
        img = tmp_path / "test.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 10)
        scores = _make_scores()

        with (
            patch("pyimgtag.commands.judge.check_ollama", return_value=(True, "")),
            patch("pyimgtag.commands.judge.scan_directory", return_value=[img]),
            patch("pyimgtag.commands.judge.OllamaClient") as MockClient,
        ):
            MockClient.return_value.judge_image.return_value = scores
            result = cmd_judge(_make_args(input_dir=str(tmp_path)), None)

        assert result == 0


class TestCmdJudgeWriteBack:
    def test_write_back_calls_write_to_photos(self, tmp_path):
        img = tmp_path / "abc123.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 10)
        scores = _make_scores()

        with (
            patch("pyimgtag.commands.judge.check_ollama", return_value=(True, "")),
            patch("pyimgtag.commands.judge.scan_photos_library", return_value=[img]),
            patch("pyimgtag.commands.judge.OllamaClient") as MockClient,
            patch("pyimgtag.commands.judge.write_to_photos", return_value=None) as mock_write,
        ):
            MockClient.return_value.judge_image.return_value = scores
            result = cmd_judge(
                _make_args(photos_library="/fake.photoslibrary", write_back=True),
                None,
            )

        assert result == 0
        mock_write.assert_called_once()
        tags_written = mock_write.call_args[0][1]
        assert any(t.startswith("score:") for t in tags_written)

    def test_write_back_false_does_not_call_write(self, tmp_path):
        img = tmp_path / "test.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 10)
        scores = _make_scores()

        with (
            patch("pyimgtag.commands.judge.check_ollama", return_value=(True, "")),
            patch("pyimgtag.commands.judge.scan_directory", return_value=[img]),
            patch("pyimgtag.commands.judge.OllamaClient") as MockClient,
            patch("pyimgtag.commands.judge.write_to_photos") as mock_write,
        ):
            MockClient.return_value.judge_image.return_value = scores
            cmd_judge(_make_args(input_dir=str(tmp_path), write_back=False), None)

        mock_write.assert_not_called()

    def test_write_back_mode_append_passed_to_writer(self, tmp_path):
        img = tmp_path / "abc123.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 10)
        scores = _make_scores()

        with (
            patch("pyimgtag.commands.judge.check_ollama", return_value=(True, "")),
            patch("pyimgtag.commands.judge.scan_photos_library", return_value=[img]),
            patch("pyimgtag.commands.judge.OllamaClient") as MockClient,
            patch("pyimgtag.commands.judge.write_to_photos", return_value=None) as mock_write,
        ):
            MockClient.return_value.judge_image.return_value = scores
            cmd_judge(
                _make_args(
                    photos_library="/fake.photoslibrary",
                    write_back=True,
                    write_back_mode="append",
                ),
                None,
            )

        call_kwargs = mock_write.call_args[1]
        assert call_kwargs.get("mode") == "append"
