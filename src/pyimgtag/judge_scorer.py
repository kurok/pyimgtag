"""Score computation for photo-judge results."""

from __future__ import annotations

from pyimgtag.models import JudgeScores


def compute_scores(scores: JudgeScores) -> tuple[int, int, int]:
    """Return (weighted_score, core_score, visible_score) — all equal to scores.score."""
    return scores.score, scores.score, scores.score


def strongest(scores: JudgeScores, n: int = 3) -> list[str]:
    """Return an empty list; per-criterion breakdown no longer exists."""
    return []


def weakest(scores: JudgeScores, n: int = 3) -> list[str]:
    """Return an empty list; per-criterion breakdown no longer exists."""
    return []
