"""Weighted score computation for photo-judge rubric."""

from __future__ import annotations

from pyimgtag.models import JudgeScores

WEIGHTS: dict[str, int] = {
    "impact": 10,
    "story_subject": 8,
    "composition_center": 10,
    "lighting": 8,
    "creativity_style": 6,
    "color_mood": 5,
    "presentation_crop": 4,
    "technical_excellence": 9,
    "focus_sharpness": 8,
    "exposure_tonal": 6,
    "noise_cleanliness": 4,
    "subject_separation": 3,
    "edit_integrity": 4,
}

_CORE = [
    "impact",
    "story_subject",
    "composition_center",
    "lighting",
    "creativity_style",
    "color_mood",
    "presentation_crop",
    "technical_excellence",
]
_VISIBLE = [
    "focus_sharpness",
    "exposure_tonal",
    "noise_cleanliness",
    "subject_separation",
    "edit_integrity",
]


def _wavg(vals: dict[str, float], keys: list[str]) -> float:
    total = sum(vals[k] * WEIGHTS[k] for k in keys)
    weight = sum(WEIGHTS[k] for k in keys)
    return total / weight if weight else 0.0


def compute_scores(scores: JudgeScores) -> tuple[int, int, int]:
    """Return (weighted_total, core_score, visible_score) each as an integer 1-10.

    The weighted average over rubric criteria is computed in floating point
    and then rounded to the nearest integer so the rating system has no
    decimal component anywhere it is displayed or stored.
    """
    d = {k: float(getattr(scores, k)) for k in WEIGHTS}
    return (
        round(_wavg(d, list(WEIGHTS.keys()))),
        round(_wavg(d, _CORE)),
        round(_wavg(d, _VISIBLE)),
    )


def strongest(scores: JudgeScores, n: int = 3) -> list[str]:
    """Return the n criterion keys with the highest scores."""
    d = {k: float(getattr(scores, k)) for k in WEIGHTS}
    return sorted(d, key=lambda k: d[k], reverse=True)[:n]


def weakest(scores: JudgeScores, n: int = 3) -> list[str]:
    """Return the n criterion keys with the lowest scores."""
    d = {k: float(getattr(scores, k)) for k in WEIGHTS}
    return sorted(d, key=lambda k: d[k])[:n]
