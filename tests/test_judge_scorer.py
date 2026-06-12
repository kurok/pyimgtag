"""Tests for judge_scorer score computation."""

from __future__ import annotations

from pyimgtag.models import JudgeScores


class TestComputeScores:
    def test_score_passthrough(self):
        from pyimgtag.judge_scorer import compute_scores

        w, core, vis = compute_scores(JudgeScores(score=8))
        assert w == 8
        assert core == 8
        assert vis == 8

    def test_all_three_equal_score(self):
        from pyimgtag.judge_scorer import compute_scores

        w, core, vis = compute_scores(JudgeScores(score=5))
        assert w == core == vis == 5

    def test_returns_three_ints(self):
        from pyimgtag.judge_scorer import compute_scores

        result = compute_scores(JudgeScores(score=7))
        assert len(result) == 3
        assert all(isinstance(v, int) for v in result)


class TestStrongestWeakest:
    def test_strongest_returns_empty(self):
        from pyimgtag.judge_scorer import strongest

        assert strongest(JudgeScores(score=10)) == []

    def test_weakest_returns_empty(self):
        from pyimgtag.judge_scorer import weakest

        assert weakest(JudgeScores(score=1)) == []

    def test_strongest_n_ignored(self):
        from pyimgtag.judge_scorer import strongest

        assert strongest(JudgeScores(score=8), n=5) == []

    def test_weakest_n_ignored(self):
        from pyimgtag.judge_scorer import weakest

        assert weakest(JudgeScores(score=3), n=5) == []
