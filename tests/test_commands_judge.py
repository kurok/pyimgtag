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
        impact=4.0,
        story_subject=4.0,
        composition_center=4.0,
        lighting=4.0,
        creativity_style=4.0,
        color_mood=4.0,
        presentation_crop=4.0,
        technical_excellence=4.0,
        focus_sharpness=4.0,
        exposure_tonal=4.0,
        noise_cleanliness=4.0,
        subject_separation=4.0,
        edit_integrity=4.0,
        verdict="Good overall.",
    )
    defaults.update(overrides)
    return JudgeScores(**defaults)


class TestScoreLabel:
    """Tests for the _score_label function."""

    def test_outstanding_at_4_5(self) -> None:
        from pyimgtag.commands.judge import _score_label

        assert _score_label(4.5) == "outstanding"

    def test_outstanding_at_5_0(self) -> None:
        from pyimgtag.commands.judge import _score_label

        assert _score_label(5.0) == "outstanding"

    def test_strong_at_4_0(self) -> None:
        from pyimgtag.commands.judge import _score_label

        assert _score_label(4.0) == "strong"

    def test_strong_at_4_4(self) -> None:
        from pyimgtag.commands.judge import _score_label

        assert _score_label(4.4) == "strong"

    def test_solid_at_3_5(self) -> None:
        from pyimgtag.commands.judge import _score_label

        assert _score_label(3.5) == "solid"

    def test_acceptable_at_3_0(self) -> None:
        from pyimgtag.commands.judge import _score_label

        assert _score_label(3.0) == "acceptable"

    def test_weak_below_3_0(self) -> None:
        from pyimgtag.commands.judge import _score_label

        assert _score_label(2.9) == "weak"
        assert _score_label(1.0) == "weak"


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

        scores_high = _make_scores(impact=5.0, composition_center=5.0)
        scores_low = _make_scores(impact=1.0, composition_center=1.0)
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
