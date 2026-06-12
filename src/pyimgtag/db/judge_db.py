"""Judge-score domain: persistence and queries for the ``judge_scores`` table."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pyimgtag.models import JudgeResult

# Default limit for get_all_judge_results; kept as a named constant so it
# is easy to find and change in one place.
_DEFAULT_JUDGE_RESULTS_LIMIT: int = 200


class JudgeDB:
    """Judge-score queries over a shared SQLite connection.

    The connection (including schema and migrations) is owned by
    :class:`pyimgtag.db.progress_db.ProgressDB`; this class only issues
    domain queries against it.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        """Bind the domain helper to the facade's open connection."""
        self._conn = conn

    def save_judge_result(self, result: "JudgeResult") -> None:
        """Persist a judge scoring result. Replaces any existing entry for the same file."""
        s = result.scores
        self._conn.execute(
            """
            INSERT OR REPLACE INTO judge_scores
                (file_path, scored_at, weighted_score, core_score, visible_score,
                 verdict, reason, score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.file_path,
                datetime.now(timezone.utc).isoformat(),
                result.weighted_score,
                result.core_score,
                result.visible_score,
                s.verdict,
                s.reason,
                s.score,
            ),
        )
        self._conn.commit()

    def get_judge_result(self, file_path: str) -> dict | None:
        """Return judge scores for a file, or None if not found."""
        row = self._conn.execute(
            """SELECT weighted_score, core_score, visible_score, verdict,
                      scored_at, reason, score
               FROM judge_scores WHERE file_path = ?""",
            (file_path,),
        ).fetchone()
        if row is None:
            return None

        def _i(v: Any) -> int:
            return int(round(float(v))) if v is not None else 0

        return {
            "file_path": file_path,
            "weighted_score": _i(row[0]),
            "core_score": _i(row[1]),
            "visible_score": _i(row[2]),
            "verdict": row[3],
            "scored_at": row[4],
            "reason": row[5],
            "score": _i(row[6]),
        }

    def get_all_judge_results(self, limit: int | None = _DEFAULT_JUDGE_RESULTS_LIMIT) -> list[dict]:
        """Return all judge scores ordered by weighted_score descending.

        Args:
            limit: Max rows to return. None = no limit (caution: may be large).

        Returns:
            List of dicts with keys: file_path, file_name, weighted_score,
            core_score, visible_score, verdict, scored_at.
        """
        limit_clause = f"LIMIT {int(limit)}" if limit is not None else ""
        rows = self._conn.execute(
            "SELECT file_path, weighted_score, core_score, visible_score, verdict, "
            "scored_at, reason "
            "FROM judge_scores "
            "ORDER BY weighted_score DESC " + limit_clause  # nosec B608
        ).fetchall()

        def _i(v: Any) -> int:
            return int(round(float(v))) if v is not None else 0

        return [
            {
                "file_path": row[0],
                "file_name": Path(row[0]).name,
                "weighted_score": _i(row[1]),
                "core_score": _i(row[2]),
                "visible_score": _i(row[3]),
                "verdict": row[4],
                "scored_at": row[5],
                "reason": row[6] if len(row) > 6 else None,
            }
            for row in rows
        ]

    # Whitelisted ORDER BY clauses for query_judge_results. Keeping the
    # rating_* keys distinct from the legacy ``judge_*`` clauses on
    # ``query_images`` keeps the Judge UI's API surface independent.
    _JUDGE_QUERY_SORTS: dict[str, str] = {
        "rating_desc": "js.weighted_score DESC NULLS LAST, pi.file_path ASC",
        "rating_asc": "js.weighted_score ASC NULLS LAST, pi.file_path ASC",
        "path_asc": "pi.file_path ASC",
        "path_desc": "pi.file_path DESC",
        "shot_desc": "pi.image_date DESC NULLS LAST, pi.file_path ASC",
        "shot_asc": "pi.image_date ASC NULLS LAST, pi.file_path ASC",
    }

    def query_judge_results(
        self,
        offset: int = 0,
        limit: int = 50,
        sort: str = "rating_desc",
        min_rating: int | None = None,
        max_rating: int | None = None,
    ) -> dict:
        """Return paginated judged images joined with their image metadata.

        The Judge page wants every record that has a ``judge_scores`` row
        plus the matching ``processed_images`` columns it renders next to
        the rating (file_name, scene_summary, image_date, location,
        cleanup_class). Caller-supplied rating bounds are clamped to
        ``[1, 10]`` rather than rejected, so the JS can pass the raw input
        without pre-validating.

        Args:
            offset: Row offset for pagination.
            limit: Max rows to return.
            sort: One of ``rating_desc`` (default), ``rating_asc``,
                ``path_asc``, ``path_desc``, ``shot_desc``, ``shot_asc``.
            min_rating: Inclusive lower bound on weighted_score (1-10).
            max_rating: Inclusive upper bound on weighted_score (1-10).

        Returns:
            ``{"items": [...], "total": <int>}``. Each item carries the
            keys consumed by the Judge UI: file_path, file_name,
            weighted_score, reason, verdict, image_date, scene_summary,
            nearest_city, nearest_country, cleanup_class.
        """
        conditions: list[str] = []
        params: list[object] = []
        if min_rating is not None:
            clamped_min = max(1, min(10, int(min_rating)))
            conditions.append("js.weighted_score >= ?")
            params.append(clamped_min)
        if max_rating is not None:
            clamped_max = max(1, min(10, int(max_rating)))
            conditions.append("js.weighted_score <= ?")
            params.append(clamped_max)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        order_clause = self._JUDGE_QUERY_SORTS.get(sort, self._JUDGE_QUERY_SORTS["rating_desc"])

        count_query = (  # nosec B608
            "SELECT COUNT(*) FROM judge_scores js "
            "LEFT JOIN processed_images pi ON pi.file_path = js.file_path " + where  # nosec B608
        )
        total = self._conn.execute(count_query, params).fetchone()[0]

        list_query = (  # nosec B608
            "SELECT js.file_path, js.weighted_score, js.reason, js.verdict, "
            "pi.scene_summary, pi.nearest_city, pi.nearest_country, "
            "pi.cleanup_class, pi.image_date, js.scored_at "
            "FROM judge_scores js "
            "LEFT JOIN processed_images pi ON pi.file_path = js.file_path "
            + where  # nosec B608
            + f" ORDER BY {order_clause} LIMIT ? OFFSET ?"  # nosec B608
        )
        params_list = [*params, int(limit), int(offset)]
        rows = self._conn.execute(list_query, params_list).fetchall()

        def _i(v: Any) -> int | None:
            return int(round(float(v))) if v is not None else None

        items = [
            {
                "file_path": row[0],
                "file_name": Path(row[0]).name,
                "weighted_score": _i(row[1]),
                "reason": row[2],
                "verdict": row[3],
                "scene_summary": row[4],
                "nearest_city": row[5],
                "nearest_country": row[6],
                "cleanup_class": row[7],
                "image_date": row[8],
                "scored_at": row[9],
            }
            for row in rows
        ]
        return {"items": items, "total": int(total)}
