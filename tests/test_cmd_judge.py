"""Tests for the judge subcommand handler."""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

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
    return JudgeScores(score=8, verdict="Good shot")


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
        assert saved.scores.score == 8

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


class TestJudgeOllamaChecksMatchRun:
    """``judge`` stops before any work, with the same lines as ``run`` (#391)."""

    def test_unreachable_server(self, tmp_path, capsys):
        from pyimgtag.preflight import _unreachable_message

        url = "http://localhost:11434"
        with (
            patch(
                "pyimgtag.commands.judge.check_ollama",
                return_value=(False, _unreachable_message(url)),
            ),
            patch("pyimgtag.commands.judge.scan_directory") as scan,
        ):
            rc = cmd_judge(_make_args(input_dir=str(tmp_path)), MagicMock())
        assert rc == 1
        assert capsys.readouterr().err.strip() == f"Error: {_unreachable_message(url)}"
        scan.assert_not_called()

    def test_model_not_pulled(self, tmp_path, capsys):
        with (
            patch("pyimgtag.commands.judge.check_ollama", return_value=(True, "up")),
            patch(
                "pyimgtag.commands.judge.check_ollama_model",
                return_value=(
                    False,
                    "model 'gemma4:e4b' is not pulled. Run 'ollama pull gemma4:e4b'.",
                ),
            ) as model_check,
            patch("pyimgtag.commands.judge.scan_directory") as scan,
        ):
            rc = cmd_judge(_make_args(input_dir=str(tmp_path)), MagicMock())
        assert rc == 1
        assert "Error: model 'gemma4:e4b' is not pulled." in capsys.readouterr().err
        model_check.assert_called_once_with("gemma4:e4b", "http://localhost:11434")
        scan.assert_not_called()

    def test_default_model_is_checked(self, tmp_path):
        with (
            patch("pyimgtag.commands.judge.check_ollama", return_value=(True, "up")),
            patch(
                "pyimgtag.commands.judge.check_ollama_model", return_value=(False, "missing")
            ) as model_check,
        ):
            cmd_judge(_make_args(input_dir=str(tmp_path), model=None), MagicMock())
        model_check.assert_called_once_with("gemma4:e4b", "http://localhost:11434")


class TestJudgeExitCodeAndReason:
    """``judge`` exits 3 when every call failed and says why each failed (#392)."""

    def _judge(self, tmp_path, outcomes, jobs=1):
        files = []
        for i, _ in enumerate(outcomes):
            f = tmp_path / f"p{i}.jpg"
            f.write_bytes(b"\xff\xd8\xff\xd9")
            files.append(f)

        class Client:
            def __init__(self):
                self._outcomes = iter(outcomes)
                self.last_error = None

            def judge_image(self, path):
                ok = next(self._outcomes)
                self.last_error = None if ok else "request failed: HTTP 500"
                return _make_scores() if ok else None

            def close(self):
                pass

        with (
            patch("pyimgtag.commands.judge.check_ollama", return_value=(True, "")),
            patch("pyimgtag.commands.judge.scan_directory", return_value=files),
            patch("pyimgtag.commands.judge.OllamaClient", return_value=Client()),
        ):
            return cmd_judge(_make_args(input_dir=str(tmp_path), jobs=jobs), MagicMock())

    def test_every_call_failing_exits_3_and_prints_the_reason(self, tmp_path, capsys):
        assert self._judge(tmp_path, [False, False]) == 3
        err = capsys.readouterr().err
        assert "judge failed (request failed: HTTP 500), skipping" in err

    def test_a_partial_failure_exits_0(self, tmp_path):
        assert self._judge(tmp_path, [False, True]) == 0

    def test_nothing_to_judge_exits_0(self, tmp_path):
        assert self._judge(tmp_path, []) == 0

    def test_the_session_records_the_reason(self, tmp_path):
        from pyimgtag.commands.judge import _judge_path

        client = MagicMock()
        client.judge_image.return_value = None
        client.last_error = "image could not be loaded"
        assert _judge_path(client, tmp_path / "a.jpg", _make_args()) == (
            None,
            "image could not be loaded",
        )
        client.last_error = MagicMock()  # a client without a real reason
        assert _judge_path(client, tmp_path / "a.jpg", _make_args())[1] == "unknown error"
