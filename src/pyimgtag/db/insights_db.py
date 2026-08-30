"""Library-wide aggregation queries backing ``pyimgtag insights``.

Every number here is computed SQL-side (``GROUP BY`` / ``COUNT`` /
``SUM`` over the existing tables, ``json_each`` for the tag column) so a
100k-row database is summarised in well under a second. No model calls,
no filesystem access — the only input is the SQLite connection.

The public entry point is :meth:`InsightsDB.compute`, which returns a
plain ``dict`` that is *the* JSON schema of ``pyimgtag insights --format
json`` (``schema_version`` is bumped on any incompatible change).
Sections are omitted from the result when the database has no data for
them, so a judge-less library never shows an empty "quality" block.
"""

from __future__ import annotations

import sqlite3
from typing import Any

INSIGHTS_SCHEMA_VERSION = 1

# Hard ceiling on the "top photos" list — the HTML report inlines a
# thumbnail per entry, so this bounds the report size.
MAX_TOP_PHOTOS = 50


class InsightsDB:
    """Aggregate statistics over ``processed_images`` / ``judge_scores`` / ``faces``."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # --- helpers -----------------------------------------------------------

    def _scalar(self, sql: str, params: tuple = ()) -> Any:
        row = self._conn.execute(sql, params).fetchone()
        return row[0] if row else None

    def _grouped(self, column: str, limit: int, where: str = "status = 'ok'") -> list[dict]:
        """``GROUP BY column`` over ok rows, non-empty values only, count desc."""
        # ``column`` is always a code-controlled identifier (never user input).
        rows = self._conn.execute(  # nosec B608
            f"SELECT {column}, COUNT(*) AS cnt FROM processed_images "  # nosec B608
            f"WHERE {where} AND {column} IS NOT NULL AND {column} != '' "
            f"GROUP BY {column} ORDER BY cnt DESC, {column} LIMIT ?",
            (limit,),
        ).fetchall()
        return [{"value": r[0], "count": r[1]} for r in rows]

    # --- sections ----------------------------------------------------------

    def _overview(self) -> dict:
        rows = self._conn.execute(
            "SELECT COALESCE(status, 'unknown'), COUNT(*) FROM processed_images "
            "GROUP BY status ORDER BY COUNT(*) DESC"
        ).fetchall()
        by_status = {r[0]: r[1] for r in rows}
        total = sum(by_status.values())
        size_bytes = self._scalar("SELECT COALESCE(SUM(file_size), 0) FROM processed_images")
        date_row = self._conn.execute(
            "SELECT MIN(image_date), MAX(image_date) FROM processed_images "
            "WHERE image_date IS NOT NULL AND image_date != ''"
        ).fetchone()
        return {
            "total": total,
            "ok": by_status.get("ok", 0),
            "error": by_status.get("error", 0),
            "by_status": by_status,
            "size_bytes": int(size_bytes or 0),
            "oldest": date_row[0] if date_row else None,
            "newest": date_row[1] if date_row else None,
        }

    def _time(self) -> dict | None:
        dated = self._scalar(
            "SELECT COUNT(*) FROM processed_images "
            "WHERE image_date IS NOT NULL AND length(image_date) >= 7"
        )
        if not dated:
            return None
        # image_date is ISO-8601 text ("YYYY-MM-DD..."), so substr() slices
        # year / month / day without any date parsing.
        per_year = self._conn.execute(
            "SELECT substr(image_date, 1, 4) AS y, COUNT(*) FROM processed_images "
            "WHERE image_date IS NOT NULL AND length(image_date) >= 7 "
            "GROUP BY y ORDER BY y"
        ).fetchall()
        per_month = self._conn.execute(
            "SELECT substr(image_date, 1, 7) AS m, COUNT(*) FROM processed_images "
            "WHERE image_date IS NOT NULL AND length(image_date) >= 7 "
            "GROUP BY m ORDER BY m"
        ).fetchall()
        busiest_month = max(per_month, key=lambda r: (r[1], r[0])) if per_month else None
        busiest_day = self._conn.execute(
            "SELECT substr(image_date, 1, 10) AS d, COUNT(*) AS cnt FROM processed_images "
            "WHERE image_date IS NOT NULL AND length(image_date) >= 10 "
            "GROUP BY d ORDER BY cnt DESC, d LIMIT 1"
        ).fetchone()
        return {
            "dated": dated,
            "per_year": [{"period": r[0], "count": r[1]} for r in per_year],
            "per_month": [{"period": r[0], "count": r[1]} for r in per_month],
            "busiest_month": (
                {"period": busiest_month[0], "count": busiest_month[1]} if busiest_month else None
            ),
            "busiest_day": (
                {"period": busiest_day[0], "count": busiest_day[1]} if busiest_day else None
            ),
        }

    def _places(self, top_n: int) -> dict | None:
        ok_total = self._scalar("SELECT COUNT(*) FROM processed_images WHERE status = 'ok'")
        located = self._scalar(
            "SELECT COUNT(*) FROM processed_images WHERE status = 'ok' AND ("
            "(nearest_country IS NOT NULL AND nearest_country != '') OR "
            "(nearest_city IS NOT NULL AND nearest_city != ''))"
        )
        if not located:
            return None
        return {
            "located": located,
            "coverage_pct": round(100.0 * located / ok_total, 1) if ok_total else 0.0,
            "countries": self._grouped("nearest_country", top_n),
            "regions": self._grouped("nearest_region", top_n),
            "cities": self._grouped("nearest_city", top_n),
        }

    def _content(self, top_n: int) -> dict | None:
        ok_total = self._scalar("SELECT COUNT(*) FROM processed_images WHERE status = 'ok'")
        if not ok_total:
            return None
        tag_rows = self._conn.execute(
            "SELECT LOWER(value) AS tag, COUNT(*) AS cnt "
            "FROM processed_images, json_each(processed_images.tags) "
            "WHERE status = 'ok' AND tags IS NOT NULL AND json_valid(tags) "
            "GROUP BY tag ORDER BY cnt DESC, tag LIMIT ?",
            (top_n,),
        ).fetchall()
        unique_tags = self._scalar(
            "SELECT COUNT(DISTINCT LOWER(value)) "
            "FROM processed_images, json_each(processed_images.tags) "
            "WHERE status = 'ok' AND tags IS NOT NULL AND json_valid(tags)"
        )
        has_text = self._scalar(
            "SELECT COUNT(*) FROM processed_images WHERE status = 'ok' AND has_text = 1"
        )
        scenes = self._grouped("scene_category", top_n)
        tones = self._grouped("emotional_tone", top_n)
        events = self._grouped("event_hint", top_n)
        if not (tag_rows or scenes or tones or events or has_text):
            return None
        return {
            "unique_tags": int(unique_tags or 0),
            "top_tags": [{"value": r[0], "count": r[1]} for r in tag_rows],
            "scene_categories": scenes,
            "emotional_tones": tones,
            "event_hints": events,
            "has_text": int(has_text or 0),
            "has_text_pct": round(100.0 * (has_text or 0) / ok_total, 1),
        }

    def _people(self, top_n: int) -> dict | None:
        rows = self._conn.execute(
            "SELECT p.id, p.label, COUNT(DISTINCT f.image_path) AS photos "
            "FROM persons p JOIN faces f ON f.person_id = p.id AND f.ignored = 0 "
            "WHERE p.label IS NOT NULL AND p.label != '' "
            "GROUP BY p.id, p.label ORDER BY photos DESC, p.label LIMIT ?",
            (top_n,),
        ).fetchall()
        if not rows:
            return None
        named_total = self._scalar(
            "SELECT COUNT(*) FROM persons WHERE label IS NOT NULL AND label != ''"
        )
        faces_total = self._scalar("SELECT COUNT(*) FROM faces WHERE ignored = 0")
        people = []
        for person_id, label, photos in rows:
            per_year = self._conn.execute(
                "SELECT substr(pi.image_date, 1, 4) AS y, COUNT(DISTINCT f.image_path) "
                "FROM faces f JOIN processed_images pi ON pi.file_path = f.image_path "
                "WHERE f.person_id = ? AND f.ignored = 0 "
                "AND pi.image_date IS NOT NULL AND length(pi.image_date) >= 4 "
                "GROUP BY y ORDER BY y",
                (person_id,),
            ).fetchall()
            people.append(
                {
                    "label": label,
                    "photos": photos,
                    "per_year": [{"period": r[0], "count": r[1]} for r in per_year],
                }
            )
        return {
            "named_persons": int(named_total or 0),
            "faces": int(faces_total or 0),
            "top_people": people,
        }

    def _quality(self, top_n: int) -> dict | None:
        judged = self._scalar("SELECT COUNT(*) FROM judge_scores")
        if not judged:
            return None
        ok_total = self._scalar("SELECT COUNT(*) FROM processed_images WHERE status = 'ok'")
        # ``score`` (0.29.0+) is authoritative; COALESCE falls back to
        # weighted_score for rows written before the migration back-filled it.
        hist_rows = self._conn.execute(
            "SELECT CAST(ROUND(COALESCE(score, weighted_score)) AS INTEGER) AS s, COUNT(*) "
            "FROM judge_scores GROUP BY s ORDER BY s"
        ).fetchall()
        histogram = {str(i): 0 for i in range(1, 11)}
        for s, cnt in hist_rows:
            key = str(min(10, max(1, int(s or 0))))
            histogram[key] = histogram.get(key, 0) + cnt
        avg = self._scalar("SELECT AVG(COALESCE(score, weighted_score)) FROM judge_scores")
        top_rows = self._conn.execute(
            "SELECT js.file_path, COALESCE(js.score, js.weighted_score) AS s, js.verdict, "
            "js.reason, pi.scene_summary, pi.image_date "
            "FROM judge_scores js LEFT JOIN processed_images pi ON pi.file_path = js.file_path "
            "ORDER BY s DESC, js.file_path LIMIT ?",
            (min(top_n, MAX_TOP_PHOTOS),),
        ).fetchall()
        return {
            "judged": judged,
            "coverage_pct": round(100.0 * judged / ok_total, 1) if ok_total else 0.0,
            "average": round(float(avg), 2) if avg is not None else None,
            "histogram": histogram,
            "top_photos": [
                {
                    "file_path": r[0],
                    "score": r[1],
                    "verdict": r[2],
                    "reason": r[3],
                    "scene_summary": r[4],
                    "image_date": r[5],
                }
                for r in top_rows
            ],
        }

    def _housekeeping(self) -> dict | None:
        total = self._scalar("SELECT COUNT(*) FROM processed_images")
        if not total:
            return None
        cleanup_rows = self._conn.execute(
            "SELECT cleanup_class, COUNT(*), COALESCE(SUM(file_size), 0) FROM processed_images "
            "WHERE cleanup_class IN ('delete', 'review') GROUP BY cleanup_class"
        ).fetchall()
        cleanup = {r[0]: {"count": r[1], "bytes": int(r[2])} for r in cleanup_rows}
        untagged = self._scalar(
            "SELECT COUNT(*) FROM processed_images WHERE status = 'ok' AND "
            "(tags IS NULL OR tags = '' OR tags = '[]')"
        )
        errors = self._scalar("SELECT COUNT(*) FROM processed_images WHERE status = 'error'")
        return {
            "delete_candidates": cleanup.get("delete", {"count": 0, "bytes": 0}),
            "review_candidates": cleanup.get("review", {"count": 0, "bytes": 0}),
            "untagged": int(untagged or 0),
            "errors": int(errors or 0),
        }

    # --- public ------------------------------------------------------------

    def compute(self, top_n: int = 10) -> dict:
        """Return the full insights document.

        Args:
            top_n: Length of every "top N" list (tags, places, people, photos).

        Returns:
            A JSON-serialisable dict. ``overview`` is always present; the
            ``time`` / ``places`` / ``content`` / ``people`` / ``quality`` /
            ``housekeeping`` keys appear only when the DB holds data for them.
            ``empty`` is ``True`` when the database has no rows at all.
        """
        top_n = max(1, min(int(top_n), MAX_TOP_PHOTOS))
        overview = self._overview()
        doc: dict[str, Any] = {
            "schema_version": INSIGHTS_SCHEMA_VERSION,
            "empty": overview["total"] == 0,
            "overview": overview,
        }
        if doc["empty"]:
            return doc
        for key, section in (
            ("time", self._time()),
            ("places", self._places(top_n)),
            ("content", self._content(top_n)),
            ("people", self._people(top_n)),
            ("quality", self._quality(top_n)),
            ("housekeeping", self._housekeeping()),
        ):
            if section:
                doc[key] = section
        return doc
