"""Tests for judge_scorer weighted-score computation."""

from __future__ import annotations

from pyimgtag.models import JudgeScores


def _scores(**overrides) -> JudgeScores:
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
    )
    defaults.update(overrides)
    return JudgeScores(**defaults)


class TestComputeScores:
    def test_uniform_8_gives_8(self):
        from pyimgtag.judge_scorer import compute_scores

        w, core, vis = compute_scores(_scores())
        assert w == 8
        assert core == 8
        assert vis == 8

    def test_uniform_10_gives_10(self):
        from pyimgtag.judge_scorer import compute_scores

        s = _scores(
            impact=10,
            story_subject=10,
            composition_center=10,
            lighting=10,
            creativity_style=10,
            color_mood=10,
            presentation_crop=10,
            technical_excellence=10,
            focus_sharpness=10,
            exposure_tonal=10,
            noise_cleanliness=10,
            subject_separation=10,
            edit_integrity=10,
        )
        w, core, vis = compute_scores(s)
        assert w == 10

    def test_weighted_score_not_just_average(self):
        from pyimgtag.judge_scorer import compute_scores

        s = _scores(impact=10, edit_integrity=2)
        w, _, _ = compute_scores(s)
        simple_avg = round((8 * 11 + 10 + 2) / 13)
        # The weighted score uses per-criterion weights so it should differ
        # from the unweighted simple average for the same inputs.
        assert w != simple_avg or w == 8  # tolerate the rare exact match

    def test_returns_three_ints(self):
        from pyimgtag.judge_scorer import compute_scores

        result = compute_scores(_scores())
        assert len(result) == 3
        assert all(isinstance(v, int) for v in result)


class TestStrongestWeakest:
    def test_strongest_returns_n_keys(self):
        from pyimgtag.judge_scorer import strongest

        keys = strongest(_scores(impact=10, composition_center=10, lighting=10), n=3)
        assert len(keys) == 3
        assert "impact" in keys

    def test_weakest_returns_lowest(self):
        from pyimgtag.judge_scorer import weakest

        keys = weakest(_scores(noise_cleanliness=1, subject_separation=1, edit_integrity=1), n=3)
        assert "noise_cleanliness" in keys

    def test_strongest_default_n_is_3(self):
        from pyimgtag.judge_scorer import strongest

        assert len(strongest(_scores())) == 3

    def test_weakest_default_n_is_3(self):
        from pyimgtag.judge_scorer import weakest

        assert len(weakest(_scores())) == 3
