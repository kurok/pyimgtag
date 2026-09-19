"""Events and trips domain: persistence, membership, and stable identity.

Owns the ``events``, ``event_members`` and ``trips`` tables (schema and
migrations live in :class:`pyimgtag.db.progress_db.ProgressDB`).

The interesting problem here is **identity across re-runs**. Clustering is
recomputed from scratch every time ``events detect`` runs, but an event a user
has renamed -- or turned into an Apple Photos album -- must keep its id when
new photos arrive. :meth:`reconcile` matches freshly computed clusters against
stored ones by membership overlap and reuses the id of the best match, so
adding a photo to last June extends that event rather than replacing it with a
stranger that happens to hold the same photos.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

#: Two clusters are the same event when this much of the stored membership
#: survives. Below it, the occasion has changed enough to be a new one.
_OVERLAP_THRESHOLD = 0.5


class EventsDB:
    """Event/trip queries over a shared SQLite connection.

    The connection (including schema and migrations) is owned by
    :class:`pyimgtag.db.progress_db.ProgressDB`; this class only issues domain
    queries against it.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        """Bind the domain helper to the facade's open connection."""
        self._conn = conn

    # --- reads -------------------------------------------------------------

    def list_events(self, limit: int | None = None) -> list[dict]:
        """Every event, newest first, with its photo count and place."""
        sql = """
            SELECT e.id, e.name, e.named_by, e.started_at, e.ended_at, e.place,
                   e.trip_id, COUNT(m.file_path) AS photo_count
              FROM events e
              LEFT JOIN event_members m ON m.event_id = e.id
             GROUP BY e.id
             ORDER BY e.started_at DESC, e.id DESC
        """
        if limit:
            sql += f" LIMIT {int(limit)}"  # nosec B608 — int-cast, not interpolated input
        # The shared connection returns plain tuples (no row_factory is set on
        # it), so rows are mapped by position rather than by dict(row).
        return [self._row_to_event(row, photo_count=row[7]) for row in self._conn.execute(sql)]

    @staticmethod
    def _row_to_event(row: tuple, photo_count: int | None = None) -> dict:
        """Map an ``events`` row (in column order) to a dict."""
        event = {
            "id": int(row[0]),
            "name": row[1],
            "named_by": row[2],
            "started_at": row[3],
            "ended_at": row[4],
            "place": row[5],
            "trip_id": row[6],
        }
        if photo_count is not None:
            event["photo_count"] = int(photo_count)
        return event

    def get_event(self, event_id: int) -> dict | None:
        """One event with its members, or None when the id is unknown."""
        row = self._conn.execute(
            "SELECT id, name, named_by, started_at, ended_at, place, trip_id"
            " FROM events WHERE id = ?",
            (event_id,),
        ).fetchone()
        if row is None:
            return None
        event = self._row_to_event(row)
        event["members"] = [
            r[0]
            for r in self._conn.execute(
                "SELECT file_path FROM event_members WHERE event_id = ? ORDER BY file_path",
                (event_id,),
            ).fetchall()
        ]
        return event

    def event_paths(self, event_id: int) -> set[str]:
        """The member paths of one event."""
        return {
            r[0]
            for r in self._conn.execute(
                "SELECT file_path FROM event_members WHERE event_id = ?", (event_id,)
            ).fetchall()
        }

    def event_for_path(self, file_path: str) -> int | None:
        """The event a photo belongs to, if any."""
        row = self._conn.execute(
            "SELECT event_id FROM event_members WHERE file_path = ?", (file_path,)
        ).fetchone()
        return int(row[0]) if row else None

    def event_stats(self) -> dict[str, int]:
        """Counts for `status` and the events page header."""
        events = self._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        members = self._conn.execute("SELECT COUNT(*) FROM event_members").fetchone()[0]
        trips = self._conn.execute(
            "SELECT COUNT(DISTINCT trip_id) FROM events WHERE trip_id IS NOT NULL"
        ).fetchone()[0]
        named = self._conn.execute(
            "SELECT COUNT(*) FROM events WHERE named_by IS NOT NULL AND named_by != 'fallback'"
        ).fetchone()[0]
        return {
            "events": int(events or 0),
            "photos": int(members or 0),
            "trips": int(trips or 0),
            "named": int(named or 0),
        }

    # --- writes ------------------------------------------------------------

    def rename_event(self, event_id: int, name: str) -> bool:
        """Set a user-chosen name; returns False when the id is unknown.

        ``named_by`` becomes ``'user'``, which is what stops ``events name``
        from overwriting it on the next run.
        """
        cur = self._conn.execute(
            "UPDATE events SET name = ?, named_by = 'user' WHERE id = ?", (name, event_id)
        )
        self._conn.commit()
        return bool(cur.rowcount)

    def set_event_name(self, event_id: int, name: str, named_by: str) -> None:
        """Set a generated name, recording which source produced it."""
        self._conn.execute(
            "UPDATE events SET name = ?, named_by = ? WHERE id = ?", (name, named_by, event_id)
        )
        self._conn.commit()

    def unnamed_events(self) -> list[dict]:
        """Events still carrying a fallback name, for ``events name``."""
        return [
            self._row_to_event(row)
            for row in self._conn.execute(
                "SELECT id, name, named_by, started_at, ended_at, place, trip_id"
                " FROM events WHERE named_by IS NULL OR named_by = 'fallback'"
                " ORDER BY started_at"
            ).fetchall()
        ]

    def reconcile(self, clusters: list[dict]) -> dict[str, int]:
        """Persist freshly detected *clusters*, reusing ids where they match.

        Each cluster is ``{"members": [path, ...], "started_at", "ended_at",
        "place", "trip", "fallback_name"}``.

        An event whose membership still overlaps a stored event by at least
        half is *that* event: its id, its name and anything built on it (an
        Apple Photos album, an exported folder) survive. Everything else is
        new. Stored events that no longer match anything are removed, because
        leaving them would accumulate empty shells over a library's life.

        Returns counts for the terminal summary.
        """
        stored = {row["id"]: self.event_paths(row["id"]) for row in self.list_events()}
        claimed: set[int] = set()
        created = updated = 0

        for cluster in clusters:
            members = set(cluster["members"])
            best_id, best_score = None, 0.0
            for event_id, paths in stored.items():
                if event_id in claimed or not paths:
                    continue
                # Overlap measured against the stored side: adding twenty
                # photos to a five-photo event should still be the same event.
                score = len(members & paths) / len(paths)
                if score > best_score:
                    best_id, best_score = event_id, score

            if best_id is not None and best_score >= _OVERLAP_THRESHOLD:
                claimed.add(best_id)
                self._update_event(best_id, cluster)
                updated += 1
            else:
                self._insert_event(cluster)
                created += 1

        removed = 0
        for event_id in stored:
            if event_id not in claimed:
                self._conn.execute("DELETE FROM event_members WHERE event_id = ?", (event_id,))
                self._conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
                removed += 1

        self._conn.commit()
        return {"created": created, "updated": updated, "removed": removed}

    def _insert_event(self, cluster: dict) -> int:
        cur = self._conn.execute(
            """INSERT INTO events (name, named_by, started_at, ended_at, place, trip_id,
                                   detected_at)
               VALUES (?, 'fallback', ?, ?, ?, ?, ?)""",
            (
                cluster["fallback_name"],
                cluster["started_at"],
                cluster["ended_at"],
                cluster.get("place"),
                cluster.get("trip"),
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )
        event_id = int(cur.lastrowid or 0)
        self._replace_members(event_id, cluster["members"])
        return event_id

    def _update_event(self, event_id: int, cluster: dict) -> None:
        # The name is deliberately not touched: a user rename, or a model name
        # already paid for, outlives a re-detection that only shifted members.
        self._conn.execute(
            """UPDATE events SET started_at = ?, ended_at = ?, place = ?, trip_id = ?
                WHERE id = ?""",
            (
                cluster["started_at"],
                cluster["ended_at"],
                cluster.get("place"),
                cluster.get("trip"),
                event_id,
            ),
        )
        self._replace_members(event_id, cluster["members"])

    def _replace_members(self, event_id: int, members: list[str]) -> None:
        self._conn.execute("DELETE FROM event_members WHERE event_id = ?", (event_id,))
        self._conn.executemany(
            "INSERT OR REPLACE INTO event_members (event_id, file_path) VALUES (?, ?)",
            [(event_id, path) for path in members],
        )

    def clear_events(self) -> int:
        """Drop every event; returns how many were removed."""
        count = self._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        self._conn.execute("DELETE FROM event_members")
        self._conn.execute("DELETE FROM events")
        self._conn.commit()
        return int(count or 0)
