"""Tests for judge_scorer weighted-score computation."""

from __future__ import annotations

import pytest
from pyimgtag.models import JudgeScores


def _scores(**overrides) -> JudgeScores:
    defaults = dict(
        impact=4.0, story_subject=4.0, composition_center=4.0,
        lighting=4.0, creativity_style=4.0, color_mood=4.0,
        presentation_crop=4.0, technical_excellence=4.0,
        focus_sharpness=4.0, exposure_tonal=4.0, noise_cleanliness=4.0,
        subject_separation=4.0, edit_integrity=4.0,
    )
    defaults.update(overrides)
    return JudgeScores(**defaults)


class TestComputeScores:
    def test_uniform_4_gives_4(self):
        from pyimgtag.judge_scorer import compute_scores
        w, core, vis = compute_scores(_scores())
        assert abs(w - 4.0) < 0.001
        assert abs(core - 4.0) < 0.001
        assert abs(vis - 4.0) < 0.001

    def test_uniform_5_gives_5(self):
        from pyimgtag.judge_scorer import compute_scores
        s = _scores(
            impact=5.0, story_subject=5.0, composition_center=5.0,
            lighting=5.0, creativity_style=5.0, color_mood=5.0,
            presentation_crop=5.0, technical_excellence=5.0,
            focus_sharpness=5.0, exposure_tonal=5.0, noise_cleanliness=5.0,
            subject_separation=5.0, edit_integrity=5.0,
        )
        w, core, vis = compute_scores(s)
        assert abs(w - 5.0) < 0.001

    def test_weighted_score_not_just_average(self):
        from pyimgtag.judge_scorer import compute_scores
        s = _scores(impact=5.0, edit_integrity=1.0)
        w, _, _ = compute_scores(s)
        simple_avg = (4.0 * 11 + 5.0 + 1.0) / 13
        assert w != pytest.approx(simple_avg, abs=0.001)

    def test_returns_three_floats(self):
        from pyimgtag.judge_scorer import compute_scores
        result = compute_scores(_scores())
        assert len(result) == 3
        assert all(isinstance(v, float) for v in result)


class TestStrongestWeakest:
    def test_strongest_returns_n_keys(self):
        from pyimgtag.judge_scorer import strongest
        keys = strongest(_scores(impact=5.0, composition_center=5.0, lighting=5.0), n=3)
        assert len(keys) == 3
        assert "impact" in keys

    def test_weakest_returns_lowest(self):
        from pyimgtag.judge_scorer import weakest
        keys = weakest(_scores(noise_cleanliness=1.0, subject_separation=1.0, edit_integrity=1.0), n=3)
        assert "noise_cleanliness" in keys

    def test_strongest_default_n_is_3(self):
        from pyimgtag.judge_scorer import strongest
        assert len(strongest(_scores())) == 3

    def test_weakest_default_n_is_3(self):
        from pyimgtag.judge_scorer import weakest
        assert len(weakest(_scores())) == 3
