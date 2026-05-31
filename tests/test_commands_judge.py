"""Tests for the judge subcommand."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch


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
        impact=8,
        story_subject=8,
        composition_center=8,
        lighting=8,
        creativity_style=8,
        color_mood=8,
        presentation_crop=8,
        technical_excellence=8,
        focus_sharpness=8,
        exposure_tonal=8,
        noise_cleanliness=8,
        subject_separation=8,
        edit_integrity=8,
        verdict="Good overall.",
    )
    defaults.update(overrides)
    return JudgeScores(**defaults)


class TestPrintVerbose:
    """Cover the reason-printing branch of _print_verbose (line 52)."""

    def test_verbose_prints_reason(self, capsys) -> None:
        from pyimgtag.commands.judge import _print_verbose
        from pyimgtag.models import JudgeResult

        scores = _make_scores(reason="Striking composition and light.")
        result = JudgeResult(
            file_path="/x/photo.jpg",
            file_name="photo.jpg",
            scores=scores,
            weighted_score=8,
            core_score=8,
            visible_score=8,
        )
        _print_verbose(result, 1, 1)
        out = capsys.readouterr().out
        assert "Reason:" in out
        assert "Striking composition" in out

    def test_verbose_legacy_verdict_branch(self, capsys) -> None:
        """No reason but a verdict prints the legacy 13-criterion summary (lines 53-59)."""
        from pyimgtag.commands.judge import _print_verbose
        from pyimgtag.models import JudgeResult

        scores = _make_scores(reason=None, verdict="A legacy verdict.")
        result = JudgeResult(
            file_path="/x/photo.jpg",
            file_name="photo.jpg",
            scores=scores,
            weighted_score=8,
            core_score=8,
            visible_score=8,
        )
        _print_verbose(result, 1, 1)
        out = capsys.readouterr().out
        assert "Verdict:" in out
        assert "Best:" in out
        assert "Weakest:" in out


class TestCmdJudgeCloudBackend:
    """Cover the non-ollama cloud backend branch (lines 158-169)."""

    def test_cloud_backend_uses_make_image_client(self, tmp_path: Path) -> None:
        from pyimgtag.commands.judge import cmd_judge

        (tmp_path / "photo.jpg").write_bytes(b"x")
        args = _make_args(tmp_path, backend="openai", api_key="k", api_base="https://api")

        with (
            patch("pyimgtag.commands.judge.make_image_client") as mock_make,
            patch("pyimgtag.commands.judge.OllamaClient") as mock_ollama,
        ):
            mock_client = MagicMock()
            mock_client.judge_image.return_value = _make_scores()
            mock_make.return_value = mock_client
            rc = cmd_judge(args, None)

        assert rc == 0
        # Cloud backend path must NOT touch OllamaClient and must not call check_ollama.
        mock_ollama.assert_not_called()
        mock_make.assert_called_once()
        assert mock_make.call_args[0][0] == "openai"

    def test_cloud_backend_error_returns_1(self, tmp_path: Path) -> None:
        from pyimgtag.cloud_clients import CloudClientError
        from pyimgtag.commands.judge import cmd_judge

        (tmp_path / "photo.jpg").write_bytes(b"x")
        args = _make_args(tmp_path, backend="openai")

        with patch(
            "pyimgtag.commands.judge.make_image_client",
            side_effect=CloudClientError("missing api key"),
        ):
            rc = cmd_judge(args, None)

        assert rc == 1

    def test_non_string_backend_defaults_to_ollama(self, tmp_path: Path) -> None:
        """A non-str backend (e.g. MagicMock attr) falls back to 'ollama' (lines 104-105)."""
        from pyimgtag.commands.judge import cmd_judge

        (tmp_path / "photo.jpg").write_bytes(b"x")
        args = _make_args(tmp_path)
        # MagicMock() default for .backend is not a str
        args.backend = MagicMock()

        with (
            patch("pyimgtag.commands.judge.check_ollama", return_value=(True, "")) as mock_check,
            patch("pyimgtag.commands.judge.OllamaClient") as mock_cls,
        ):
            mock_client = MagicMock()
            mock_client.judge_image.return_value = _make_scores()
            mock_cls.return_value = mock_client
            rc = cmd_judge(args, None)

        assert rc == 0
        mock_check.assert_called_once()


class TestCmdJudgeSession:
    """Cover RunSession counter / dashboard branches via a mocked session."""

    def _session(self):
        session = MagicMock()
        # snapshot/wait helpers must be no-ops for the loop
        session.wait_if_paused.return_value = None
        return session

    def test_skip_judged_increments_session_counter(self, tmp_path: Path) -> None:
        """skip-judged with an active session hits session.increment (lines 193-194)."""
        from pyimgtag.commands.judge import cmd_judge

        (tmp_path / "scored.jpg").write_bytes(b"x")
        args = _make_args(tmp_path)
        args.skip_judged = True

        db = MagicMock()
        db.get_judge_result.return_value = {"weighted_score": 7}

        session = self._session()
        dashboard = MagicMock()

        with (
            patch("pyimgtag.commands.judge.check_ollama", return_value=(True, "")),
            patch("pyimgtag.commands.judge.OllamaClient") as mock_cls,
            patch(
                "pyimgtag.commands.judge.start_dashboard_for",
                return_value=(session, dashboard),
            ),
        ):
            mock_client = MagicMock()
            mock_client.judge_image.return_value = _make_scores()
            mock_cls.return_value = mock_client
            rc = cmd_judge(args, db)

        assert rc == 0
        session.increment.assert_any_call("skipped_already_judged")
        # Model never invoked for the already-judged file.
        mock_client.judge_image.assert_not_called()
        # finally-block must stop the dashboard (line 271).
        dashboard.stop.assert_called_once()

    def test_judge_failed_records_session_error(self, tmp_path: Path) -> None:
        """judge_image returning None with a session records an error (lines 204-206)."""
        from pyimgtag.commands.judge import cmd_judge

        (tmp_path / "photo.jpg").write_bytes(b"x")
        args = _make_args(tmp_path)

        session = self._session()
        dashboard = MagicMock()

        with (
            patch("pyimgtag.commands.judge.check_ollama", return_value=(True, "")),
            patch("pyimgtag.commands.judge.OllamaClient") as mock_cls,
            patch(
                "pyimgtag.commands.judge.start_dashboard_for",
                return_value=(session, dashboard),
            ),
        ):
            mock_client = MagicMock()
            mock_client.judge_image.return_value = None
            mock_cls.return_value = mock_client
            rc = cmd_judge(args, None)

        assert rc == 0
        session.increment.assert_any_call("judge_failed")
        session.record_item.assert_any_call(
            str(tmp_path / "photo.jpg"), "error", error="judge failed"
        )
        dashboard.stop.assert_called_once()

    def test_min_score_skip_increments_session(self, tmp_path: Path) -> None:
        """A below-threshold score with a session hits skipped_min_score (lines 221-222)."""
        from pyimgtag.commands.judge import cmd_judge

        (tmp_path / "photo.jpg").write_bytes(b"x")
        args = _make_args(tmp_path, min_score=10)

        session = self._session()
        dashboard = MagicMock()

        with (
            patch("pyimgtag.commands.judge.check_ollama", return_value=(True, "")),
            patch("pyimgtag.commands.judge.OllamaClient") as mock_cls,
            patch(
                "pyimgtag.commands.judge.start_dashboard_for",
                return_value=(session, dashboard),
            ),
        ):
            mock_client = MagicMock()
            mock_client.judge_image.return_value = _make_scores()  # ~8 < 10
            mock_cls.return_value = mock_client
            rc = cmd_judge(args, None)

        assert rc == 0
        session.increment.assert_any_call("skipped_min_score")
        dashboard.stop.assert_called_once()

    def test_keyboard_interrupt_marks_session_interrupted(self, tmp_path: Path) -> None:
        """KeyboardInterrupt during judging returns 1 and marks the session (lines 250-254)."""
        from pyimgtag.commands.judge import cmd_judge

        (tmp_path / "photo.jpg").write_bytes(b"x")
        args = _make_args(tmp_path)

        session = self._session()
        dashboard = MagicMock()

        with (
            patch("pyimgtag.commands.judge.check_ollama", return_value=(True, "")),
            patch("pyimgtag.commands.judge.OllamaClient") as mock_cls,
            patch(
                "pyimgtag.commands.judge.start_dashboard_for",
                return_value=(session, dashboard),
            ),
        ):
            mock_client = MagicMock()
            mock_client.judge_image.side_effect = KeyboardInterrupt()
            mock_cls.return_value = mock_client
            rc = cmd_judge(args, None)

        assert rc == 1
        session.mark_interrupted.assert_called_once()
        # mark_completed must NOT run when interrupted.
        session.mark_completed.assert_not_called()
        dashboard.stop.assert_called_once()


class TestScoreLabel:
    """Tests for the _score_label function (integer 1-10 scale)."""

    def test_outstanding_at_9(self) -> None:
        from pyimgtag.commands.judge import _score_label

        assert _score_label(9) == "outstanding"

    def test_outstanding_at_10(self) -> None:
        from pyimgtag.commands.judge import _score_label

        assert _score_label(10) == "outstanding"

    def test_strong_at_8(self) -> None:
        from pyimgtag.commands.judge import _score_label

        assert _score_label(8) == "strong"

    def test_solid_at_7(self) -> None:
        from pyimgtag.commands.judge import _score_label

        assert _score_label(7) == "solid"

    def test_acceptable_at_5(self) -> None:
        from pyimgtag.commands.judge import _score_label

        assert _score_label(5) == "acceptable"

    def test_acceptable_at_6(self) -> None:
        from pyimgtag.commands.judge import _score_label

        assert _score_label(6) == "acceptable"

    def test_weak_below_5(self) -> None:
        from pyimgtag.commands.judge import _score_label

        assert _score_label(4) == "weak"
        assert _score_label(1) == "weak"


class TestCmdJudgeSkipJudged:
    def test_skip_judged_skips_already_scored(self, tmp_path: Path) -> None:
        """``--skip-judged`` must cause images already in judge_scores to be
        skipped without invoking the model again — the resume-from-DB
        equivalent for repeated judge runs over the same source."""
        from pyimgtag.commands.judge import cmd_judge

        (tmp_path / "scored.jpg").write_bytes(b"x")
        (tmp_path / "fresh.jpg").write_bytes(b"x")
        args = _make_args(tmp_path)
        args.skip_judged = True

        db = MagicMock()
        # Already-scored image returns a dict; fresh image returns None.
        db.get_judge_result.side_effect = lambda p: {"weighted_score": 7} if "scored" in p else None

        with (
            patch("pyimgtag.commands.judge.check_ollama", return_value=(True, "")),
            patch("pyimgtag.commands.judge.OllamaClient") as mock_cls,
        ):
            mock_client = MagicMock()
            mock_client.judge_image.return_value = _make_scores()
            mock_cls.return_value = mock_client
            cmd_judge(args, db)

        # Only the fresh image is judged; the model is invoked exactly once.
        assert mock_client.judge_image.call_count == 1
        # And only that one judge_result is saved.
        assert db.save_judge_result.call_count == 1


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

    def test_returns_1_when_no_source(self, tmp_path: Path) -> None:
        """cmd_judge must return 1 when neither --input-dir nor --photos-library is given."""
        from pyimgtag.commands.judge import cmd_judge

        args = _make_args(tmp_path)
        args.input_dir = None
        args.photos_library = None

        with patch("pyimgtag.commands.judge.check_ollama", return_value=(True, "")):
            rc = cmd_judge(args, MagicMock())

        assert rc == 1

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
        args = _make_args(tmp_path, min_score=9)

        with (
            patch("pyimgtag.commands.judge.check_ollama", return_value=(True, "")),
            patch("pyimgtag.commands.judge.OllamaClient") as mock_cls,
        ):
            mock_client = MagicMock()
            mock_client.judge_image.return_value = _make_scores()  # weighted ~8, below 9
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

    def test_limit_applied(self, tmp_path: Path) -> None:
        """Only first N files are scored when --limit is set."""
        from pyimgtag.commands.judge import cmd_judge

        for i in range(5):
            (tmp_path / f"photo{i}.jpg").write_bytes(b"x")
        args = _make_args(tmp_path, limit=2)

        with (
            patch("pyimgtag.commands.judge.check_ollama", return_value=(True, "")),
            patch("pyimgtag.commands.judge.OllamaClient") as mock_cls,
        ):
            mock_client = MagicMock()
            mock_client.judge_image.return_value = _make_scores()
            mock_cls.return_value = mock_client
            cmd_judge(args, MagicMock())

        assert mock_client.judge_image.call_count == 2

    def test_judge_failure_skipped(self, tmp_path: Path) -> None:
        """Files where judge_image returns None are skipped gracefully."""
        from pyimgtag.commands.judge import cmd_judge

        (tmp_path / "photo.jpg").write_bytes(b"x")
        args = _make_args(tmp_path)

        with (
            patch("pyimgtag.commands.judge.check_ollama", return_value=(True, "")),
            patch("pyimgtag.commands.judge.OllamaClient") as mock_cls,
        ):
            mock_client = MagicMock()
            mock_client.judge_image.return_value = None  # simulate failure
            mock_cls.return_value = mock_client
            rc = cmd_judge(args, MagicMock())

        assert rc == 0  # must not crash

    def test_sort_by_score_descending(self, tmp_path: Path) -> None:
        """Results sorted by score descending when sort_by='score'."""
        import json

        from pyimgtag.commands.judge import cmd_judge

        (tmp_path / "a.jpg").write_bytes(b"x")
        (tmp_path / "b.jpg").write_bytes(b"x")
        out = tmp_path / "out.json"
        args = _make_args(tmp_path, sort_by="score", output_json=str(out))

        scores_high = _make_scores(impact=10, composition_center=10)
        scores_low = _make_scores(impact=1, composition_center=1)
        call_count = 0

        def fake_judge(path):
            nonlocal call_count
            call_count += 1
            return scores_high if call_count == 1 else scores_low

        with (
            patch("pyimgtag.commands.judge.check_ollama", return_value=(True, "")),
            patch("pyimgtag.commands.judge.OllamaClient") as mock_cls,
        ):
            mock_client = MagicMock()
            mock_client.judge_image.side_effect = fake_judge
            mock_cls.return_value = mock_client
            cmd_judge(args, MagicMock())

        data = json.loads(out.read_text())
        assert data[0]["weighted_score"] >= data[1]["weighted_score"]

    def test_sort_by_name(self, tmp_path: Path) -> None:
        """Results sorted by filename when sort_by='name'."""
        import json

        from pyimgtag.commands.judge import cmd_judge

        (tmp_path / "z.jpg").write_bytes(b"x")
        (tmp_path / "a.jpg").write_bytes(b"x")
        out = tmp_path / "out.json"
        args = _make_args(tmp_path, sort_by="name", output_json=str(out))

        with (
            patch("pyimgtag.commands.judge.check_ollama", return_value=(True, "")),
            patch("pyimgtag.commands.judge.OllamaClient") as mock_cls,
        ):
            mock_client = MagicMock()
            mock_client.judge_image.return_value = _make_scores()
            mock_cls.return_value = mock_client
            cmd_judge(args, MagicMock())

        data = json.loads(out.read_text())
        names = [d["file_name"] for d in data]
        assert names == sorted(names)

    def test_verbose_output(self, tmp_path: Path) -> None:
        """--verbose flag uses _print_verbose path."""
        from pyimgtag.commands.judge import cmd_judge

        (tmp_path / "photo.jpg").write_bytes(b"x")
        args = _make_args(tmp_path, verbose=True)

        with (
            patch("pyimgtag.commands.judge.check_ollama", return_value=(True, "")),
            patch("pyimgtag.commands.judge.OllamaClient") as mock_cls,
        ):
            mock_client = MagicMock()
            mock_client.judge_image.return_value = _make_scores()
            mock_cls.return_value = mock_client
            rc = cmd_judge(args, MagicMock())

        assert rc == 0

    def test_scan_directory_permission_error(self, tmp_path: Path) -> None:
        """PermissionError from scan_directory returns 1."""
        from pyimgtag.commands.judge import cmd_judge

        args = _make_args(tmp_path)

        with (
            patch("pyimgtag.commands.judge.check_ollama", return_value=(True, "")),
            patch(
                "pyimgtag.commands.judge.scan_directory",
                side_effect=PermissionError("denied"),
            ),
        ):
            rc = cmd_judge(args, MagicMock())

        assert rc == 1

    def test_scan_directory_file_not_found_error(self, tmp_path: Path) -> None:
        """FileNotFoundError from scan_directory returns 1."""
        from pyimgtag.commands.judge import cmd_judge

        args = _make_args(tmp_path)

        with (
            patch("pyimgtag.commands.judge.check_ollama", return_value=(True, "")),
            patch(
                "pyimgtag.commands.judge.scan_directory",
                side_effect=FileNotFoundError("not found"),
            ),
        ):
            rc = cmd_judge(args, MagicMock())

        assert rc == 1

    def test_photos_library_success(self, tmp_path: Path) -> None:
        """scan_photos_library happy path returns results."""
        from pyimgtag.commands.judge import cmd_judge

        img = tmp_path / "photo.jpg"
        img.write_bytes(b"x")
        args = _make_args(tmp_path)
        args.input_dir = None
        args.photos_library = str(tmp_path)

        with (
            patch("pyimgtag.commands.judge.check_ollama", return_value=(True, "")),
            patch("pyimgtag.commands.judge.scan_photos_library", return_value=[img]),
            patch("pyimgtag.commands.judge.OllamaClient") as mock_cls,
        ):
            mock_client = MagicMock()
            mock_client.judge_image.return_value = _make_scores()
            mock_cls.return_value = mock_client
            rc = cmd_judge(args, MagicMock())

        assert rc == 0

    def test_photos_library_permission_error(self, tmp_path: Path) -> None:
        """PermissionError from scan_photos_library returns 1."""
        from pyimgtag.commands.judge import cmd_judge

        args = _make_args(tmp_path)
        args.input_dir = None
        args.photos_library = str(tmp_path)

        with (
            patch("pyimgtag.commands.judge.check_ollama", return_value=(True, "")),
            patch(
                "pyimgtag.commands.judge.scan_photos_library",
                side_effect=PermissionError("denied"),
            ),
        ):
            rc = cmd_judge(args, MagicMock())

        assert rc == 1
