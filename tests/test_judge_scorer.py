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

        # Skew high-weight criteria (impact=10, composition_center=10,
        # technical_excellence=9) high and low-weight criteria
        # (noise_cleanliness=4, subject_separation=3, edit_integrity=4) low,
        # so the weighted average provably diverges from the simple average:
        # weighted = round(661 / 85) == 8, simple = round(89 / 13) == 7.
        s = _scores(
            impact=10,
            composition_center=10,
            technical_excellence=10,
            noise_cleanliness=1,
            subject_separation=1,
            edit_integrity=1,
        )
        w, _, _ = compute_scores(s)
        simple_avg = round((10 * 3 + 8 * 7 + 1 * 3) / 13)
        assert w == 8
        assert w != simple_avg

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
