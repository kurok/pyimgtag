"""Map and timeline aggregation over ``processed_images``.

Everything here is computed SQL-side so the ``/map`` and ``/timeline``
pages stay responsive on a 100k-row library:

* :meth:`MapDB.clusters` bins photos into a fixed grid whose cell size is
  derived from the requested zoom, using ``GROUP BY CAST(coord / cell)``
  so SQLite does one indexed scan and returns at most a few thousand rows
  instead of every coordinate.
* :meth:`MapDB.timeline_months` / :meth:`MapDB.timeline_days` reuse the
  ``substr(image_date, …)`` idiom from :mod:`pyimgtag.db.insights_db` —
  ``image_date`` is ISO-8601 text, so slicing it is the whole date parse.

No filesystem access and no model calls; the only input is the SQLite
connection owned by :class:`pyimgtag.db.progress_db.ProgressDB`.
"""

from __future__ import annotations

import sqlite3

#: Full longitude span of the world in degrees; zoom 0 is one cell wide.
_WORLD_DEGREES = 360.0

#: Web-Mercator tile zoom levels the cluster endpoint accepts.
MIN_ZOOM = 0
MAX_ZOOM = 20

#: Hard ceiling on the number of grid cells one ``clusters()`` call returns.
#: A cluster response is drawn as one marker per cell, so this bounds both
#: the JSON payload and how many DOM nodes Leaflet has to manage.
MAX_CELLS = 2000

#: At or above this zoom the UI stops clustering and lists photos directly.
LEAF_ZOOM = 15

#: Latitude clamp. Web Mercator cannot represent the poles, and clamping
#: keeps a cell index from running off the end of the grid.
_MAX_LAT = 85.05112878


def cell_size_for_zoom(zoom: int) -> float:
    """Return the grid cell size in degrees for *zoom*.

    Cells are square in degrees (``360 / 2**zoom`` on both axes), which
    makes the binning arithmetic a single division per row and matches the
    way Leaflet doubles its resolution per zoom step. The result is clamped
    to the ``MIN_ZOOM``/``MAX_ZOOM`` range.
    """
    z = max(MIN_ZOOM, min(MAX_ZOOM, int(zoom)))
    return _WORLD_DEGREES / float(2**z)


class MapDB:
    """Geographic and temporal aggregation queries for the webapp pages."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        """Bind the domain helper to the facade's open connection."""
        self._conn = conn

    # --- geography ---------------------------------------------------------

    def gps_coverage(self) -> dict[str, int]:
        """Return ``{"with_gps": N, "without_gps": M}`` over all rows.

        Powers the "N photos not on map" badge, so it counts every row in
        ``processed_images`` rather than only the successful ones.
        """
        row = self._conn.execute(
            "SELECT COUNT(*), "
            "SUM(CASE WHEN gps_lat IS NOT NULL AND gps_lon IS NOT NULL THEN 1 ELSE 0 END) "
            "FROM processed_images"
        ).fetchone()
        total = int(row[0] or 0)
        with_gps = int(row[1] or 0)
        return {"with_gps": with_gps, "without_gps": total - with_gps}

    def clusters(
        self,
        bbox: tuple[float, float, float, float] | None = None,
        zoom: int = 2,
        limit: int = MAX_CELLS,
    ) -> list[dict]:
        """Return grid-binned photo clusters for the map.

        Args:
            bbox: Optional ``(lat1, lon1, lat2, lon2)`` viewport filter,
                inclusive on every edge. ``lon1 > lon2`` is read as crossing
                the antimeridian. ``None`` bins the whole library.
            zoom: Leaflet zoom level; the cell size is
                :func:`cell_size_for_zoom`.
            limit: Maximum number of cells to return (default
                :data:`MAX_CELLS`). The busiest cells are kept.

        Returns:
            A list of ``{"lat", "lon", "count", "sample_path"}`` dicts.
            ``lat``/``lon`` are the *centroid* of the photos in the cell (the
            average, not the cell centre) so a marker sits on the photos
            rather than on an arbitrary grid intersection. ``sample_path`` is
            a deterministic representative used for the popover thumbnail.
        """
        cell = cell_size_for_zoom(zoom)
        conditions = ["gps_lat IS NOT NULL", "gps_lon IS NOT NULL"]
        # Coordinates are shifted into the non-negative quadrant before the
        # division so SQLite's truncating CAST behaves as a floor; dividing
        # signed degrees directly would fold the two cells either side of the
        # equator (and of the prime meridian) into one.
        params: list[object] = [-_MAX_LAT, _MAX_LAT, cell, cell]
        if bbox is not None:
            lat1, lon1, lat2, lon2 = bbox
            lat_lo, lat_hi = (lat1, lat2) if lat1 <= lat2 else (lat2, lat1)
            conditions.append("gps_lat BETWEEN ? AND ?")
            params.extend([lat_lo, lat_hi])
            if lon1 <= lon2:
                conditions.append("gps_lon BETWEEN ? AND ?")
            else:
                conditions.append("(gps_lon >= ? OR gps_lon <= ?)")
            params.extend([lon1, lon2])
        # Only the WHERE clause is assembled from code-controlled literals;
        # every value (clamp, cell size, bbox, limit) is a bound parameter.
        sql = (
            "SELECT CAST((MAX(?, MIN(?, gps_lat)) + 90.0) / ? AS INTEGER) AS cy, "  # nosec B608
            "CAST((gps_lon + 180.0) / ? AS INTEGER) AS cx, "
            "AVG(gps_lat), AVG(gps_lon), COUNT(*) AS cnt, MIN(file_path) "
            "FROM processed_images WHERE " + " AND ".join(conditions) + " "
            "GROUP BY cy, cx ORDER BY cnt DESC, cy, cx LIMIT ?"
        )
        params.append(max(1, min(int(limit), MAX_CELLS)))
        rows = self._conn.execute(sql, params).fetchall()
        return [
            {
                "lat": float(r[2]),
                "lon": float(r[3]),
                "count": int(r[4]),
                "sample_path": r[5],
            }
            for r in rows
        ]

    # --- time --------------------------------------------------------------

    def timeline_months(self) -> list[dict]:
        """Return per-month photo counts plus the colour-toggle metrics.

        Each entry is ``{"period": "YYYY-MM", "count": N, "avg_judge_score":
        float|None, "cleanup": {"delete": n, "review": n, "keep": n}}``.
        ``avg_judge_score`` is ``None`` for a month in which nothing was
        judged, so the UI can grey the bar out instead of drawing a zero.
        Months with no dated photos are simply absent.
        """
        rows = self._conn.execute(
            "SELECT substr(pi.image_date, 1, 7) AS m, COUNT(*), "
            "AVG(COALESCE(js.score, js.weighted_score)), "
            "SUM(CASE WHEN pi.cleanup_class = 'delete' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN pi.cleanup_class = 'review' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN pi.cleanup_class = 'keep' THEN 1 ELSE 0 END) "
            "FROM processed_images pi "
            "LEFT JOIN judge_scores js ON js.file_path = pi.file_path "
            "WHERE pi.image_date IS NOT NULL AND length(pi.image_date) >= 7 "
            "GROUP BY m ORDER BY m"
        ).fetchall()
        return [self._period_row(r) for r in rows]

    def timeline_days(self, month: str) -> list[dict]:
        """Return per-day counts and metrics for one ``YYYY-MM`` month.

        The shape matches :meth:`timeline_months` with ``period`` set to a
        ``YYYY-MM-DD`` day. An unknown or malformed month yields an empty list.
        """
        prefix = str(month)[:7]
        if len(prefix) != 7:
            return []
        rows = self._conn.execute(
            "SELECT substr(pi.image_date, 1, 10) AS d, COUNT(*), "
            "AVG(COALESCE(js.score, js.weighted_score)), "
            "SUM(CASE WHEN pi.cleanup_class = 'delete' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN pi.cleanup_class = 'review' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN pi.cleanup_class = 'keep' THEN 1 ELSE 0 END) "
            "FROM processed_images pi "
            "LEFT JOIN judge_scores js ON js.file_path = pi.file_path "
            "WHERE pi.image_date >= ? AND pi.image_date < ? "
            "AND length(pi.image_date) >= 10 "
            "GROUP BY d ORDER BY d",
            (prefix, prefix + "\uffff"),
        ).fetchall()
        return [self._period_row(r) for r in rows]

    @staticmethod
    def _period_row(row: tuple) -> dict:
        """Shape one ``(period, count, avg, delete, review, keep)`` row."""
        return {
            "period": row[0],
            "count": int(row[1]),
            "avg_judge_score": round(float(row[2]), 2) if row[2] is not None else None,
            "cleanup": {
                "delete": int(row[3] or 0),
                "review": int(row[4] or 0),
                "keep": int(row[5] or 0),
            },
        }
