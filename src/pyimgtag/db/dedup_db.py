"""Duplicate-group domain: the ``dedup_groups`` / ``dedup_members`` tables.

``pyimgtag dedup scan`` stores a perceptual hash (plus pixel dimensions) on
every ``processed_images`` row, groups the hashes, and persists the result
here so ``list`` / ``resolve`` / ``undo`` and the ``/dedup`` page all read the
same plan.

Incremental rescans follow one rule, deliberately simple so the safety story
stays explainable:

* **Resolved groups are frozen.** Once a group has a ``resolved_at`` stamp its
  rows are never rewritten, and its members are excluded from newly built
  groups — a photo you already dealt with cannot reappear in a fresh plan.
* **Unresolved groups are rebuilt** on every scan. A rebuilt group keeps its
  ``id`` when it overlaps exactly one previous unresolved group, so a new photo
  matching an existing group *joins* it rather than creating a stranger.

Schema and migrations live in :class:`pyimgtag.db.progress_db.ProgressDB`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

#: Action recorded for a loser that was relocated to the quarantine directory.
ACTION_MOVE = "move"
#: Action recorded for a loser sent to the OS trash.
ACTION_TRASH = "trash"
#: Action recorded for an Apple Photos original: keyword only, never a disk edit.
ACTION_TAG = "tag"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DedupDB:
    """Duplicate-group queries over a shared SQLite connection."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        """Bind the domain helper to the facade's open connection."""
        self._conn = conn

    # --- phash bookkeeping -------------------------------------------------

    def iter_paths_missing_phash(self, include_hashed: bool = False) -> "Iterator[str]":
        """Yield DB-known paths that still need hashing.

        Args:
            include_hashed: When True, yield every known path so ``dedup scan
                --rehash`` recomputes hashes that are already stored.
        """
        if include_hashed:
            rows = self._conn.execute(
                "SELECT file_path FROM processed_images ORDER BY file_path"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT file_path FROM processed_images "
                "WHERE phash IS NULL OR phash = '' OR width IS NULL OR height IS NULL "
                "ORDER BY file_path"
            ).fetchall()
        for (path,) in rows:
            yield path

    def set_phash(
        self,
        file_path: str,
        phash: str | None,
        width: int | None = None,
        height: int | None = None,
    ) -> None:
        """Store the perceptual hash and pixel dimensions for one row."""
        self._conn.execute(
            "UPDATE processed_images SET phash = ?, width = ?, height = ? WHERE file_path = ?",
            (phash, width, height, file_path),
        )
        self._conn.commit()

    def all_phashes(self) -> list[tuple[str, str]]:
        """Return every ``(file_path, phash)`` pair that has a hash stored."""
        rows = self._conn.execute(
            "SELECT file_path, phash FROM processed_images "
            "WHERE phash IS NOT NULL AND phash != '' ORDER BY file_path"
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    # --- group bookkeeping -------------------------------------------------

    def _locked_paths(self) -> set[str]:
        """Paths belonging to a resolved group; excluded from new plans."""
        rows = self._conn.execute(
            "SELECT dm.file_path FROM dedup_members dm "
            "JOIN dedup_groups g ON g.id = dm.group_id "
            "WHERE g.resolved_at IS NOT NULL"
        ).fetchall()
        return {r[0] for r in rows}

    def replace_unresolved_groups(
        self,
        groups: Sequence[tuple[str, Sequence[str]]],
        threshold: int,
    ) -> int:
        """Rewrite the unresolved plan from a fresh scan.

        Args:
            groups: ``(kind, paths)`` pairs as produced by
                :func:`pyimgtag.dedup_groups.group_by_phash`.
            threshold: The Hamming threshold the scan used; stored per group.

        Returns:
            Number of unresolved groups after the rewrite.
        """
        locked = self._locked_paths()
        existing_rows = self._conn.execute(
            "SELECT dm.group_id, dm.file_path FROM dedup_members dm "
            "JOIN dedup_groups g ON g.id = dm.group_id "
            "WHERE g.resolved_at IS NULL"
        ).fetchall()
        path_to_group: dict[str, int] = {}
        for group_id, file_path in existing_rows:
            path_to_group.setdefault(file_path, group_id)

        keep: set[int] = set()
        with self._conn:
            for kind, paths in groups:
                members = sorted({p for p in paths if p not in locked})
                if len(members) < 2:
                    continue
                candidates = {path_to_group[p] for p in members if p in path_to_group} - keep
                group_id = min(candidates) if candidates else None
                if group_id is None:
                    cur = self._conn.execute(
                        "INSERT INTO dedup_groups (threshold, kind, created_at) VALUES (?, ?, ?)",
                        (int(threshold), kind, _now()),
                    )
                    group_id = int(cur.lastrowid or 0)
                else:
                    self._conn.execute(
                        "UPDATE dedup_groups SET threshold = ?, kind = ? WHERE id = ?",
                        (int(threshold), kind, group_id),
                    )
                    self._conn.execute("DELETE FROM dedup_members WHERE group_id = ?", (group_id,))
                self._conn.executemany(
                    "INSERT OR REPLACE INTO dedup_members (group_id, file_path) VALUES (?, ?)",
                    [(group_id, p) for p in members],
                )
                keep.add(group_id)
            # Drop every unresolved group the new plan no longer produces.
            stale = self._conn.execute(
                "SELECT id FROM dedup_groups WHERE resolved_at IS NULL"
            ).fetchall()
            for (group_id,) in stale:
                if group_id in keep:
                    continue
                self._conn.execute("DELETE FROM dedup_members WHERE group_id = ?", (group_id,))
                self._conn.execute("DELETE FROM dedup_groups WHERE id = ?", (group_id,))
        return len(keep)

    _MEMBER_SQL = (
        "SELECT dm.group_id, dm.file_path, dm.action, dm.moved_to, dm.acted_at, "
        "pi.file_size, pi.file_mtime, pi.width, pi.height, "
        "COALESCE(js.score, js.weighted_score) "
        "FROM dedup_members dm "
        "LEFT JOIN processed_images pi ON pi.file_path = dm.file_path "
        "LEFT JOIN judge_scores js ON js.file_path = dm.file_path "
    )

    @staticmethod
    def _member_row_to_dict(row: tuple) -> dict:
        return {
            "file_path": row[1],
            "action": row[2],
            "moved_to": row[3],
            "acted_at": row[4],
            "file_size": row[5],
            "file_mtime": row[6],
            "width": row[7],
            "height": row[8],
            "judge_score": float(row[9]) if row[9] is not None else None,
        }

    def list_groups(self, include_resolved: bool = False) -> list[dict]:
        """Return duplicate groups with their members.

        Args:
            include_resolved: Also return groups that already carry a
                ``resolved_at`` stamp (default: unresolved only).

        Returns:
            One dict per group: ``id``, ``kind``, ``threshold``, ``created_at``,
            ``resolved_at``, ``keep_path``, and a ``members`` list carrying
            ``file_path``, ``file_size``, ``file_mtime``, ``width``, ``height``,
            ``judge_score``, ``action``, ``moved_to``, ``acted_at``.
        """
        if include_resolved:
            group_rows = self._conn.execute(
                "SELECT id, threshold, kind, created_at, resolved_at, keep_path "
                "FROM dedup_groups ORDER BY id"
            ).fetchall()
            member_rows = self._conn.execute(self._MEMBER_SQL + "ORDER BY dm.file_path").fetchall()
        else:
            group_rows = self._conn.execute(
                "SELECT id, threshold, kind, created_at, resolved_at, keep_path "
                "FROM dedup_groups WHERE resolved_at IS NULL ORDER BY id"
            ).fetchall()
            member_rows = self._conn.execute(
                self._MEMBER_SQL
                + "JOIN dedup_groups g ON g.id = dm.group_id "
                + "WHERE g.resolved_at IS NULL ORDER BY dm.file_path"
            ).fetchall()

        members: dict[int, list[dict]] = {}
        for row in member_rows:
            members.setdefault(row[0], []).append(self._member_row_to_dict(row))
        return [
            {
                "id": g[0],
                "threshold": g[1],
                "kind": g[2],
                "created_at": g[3],
                "resolved_at": g[4],
                "keep_path": g[5],
                "members": members.get(g[0], []),
            }
            for g in group_rows
        ]

    def get_group(self, group_id: int) -> dict | None:
        """Return one group (resolved or not) with its members, or ``None``."""
        g = self._conn.execute(
            "SELECT id, threshold, kind, created_at, resolved_at, keep_path "
            "FROM dedup_groups WHERE id = ?",
            (int(group_id),),
        ).fetchone()
        if g is None:
            return None
        rows = self._conn.execute(
            self._MEMBER_SQL + "WHERE dm.group_id = ? ORDER BY dm.file_path",
            (int(group_id),),
        ).fetchall()
        return {
            "id": g[0],
            "threshold": g[1],
            "kind": g[2],
            "created_at": g[3],
            "resolved_at": g[4],
            "keep_path": g[5],
            "members": [self._member_row_to_dict(r) for r in rows],
        }

    def record_action(
        self,
        group_id: int,
        file_path: str,
        action: str,
        moved_to: str | None = None,
    ) -> None:
        """Record what was done to one group member."""
        self._conn.execute(
            "UPDATE dedup_members SET action = ?, moved_to = ?, acted_at = ? "
            "WHERE group_id = ? AND file_path = ?",
            (action, moved_to, _now(), int(group_id), file_path),
        )
        self._conn.commit()

    def mark_resolved(self, group_id: int, keep_path: str) -> None:
        """Stamp a group resolved and remember which copy was kept."""
        self._conn.execute(
            "UPDATE dedup_groups SET resolved_at = ?, keep_path = ? WHERE id = ?",
            (_now(), keep_path, int(group_id)),
        )
        self._conn.commit()

    def undo_group(self, group_id: int) -> int:
        """Clear every recorded action on a group and un-resolve it.

        The caller is responsible for putting moved files back on disk *before*
        calling this, so a failed restore leaves the DB record intact and the
        undo can be retried.

        Returns:
            Number of member rows cleared.
        """
        with self._conn:
            cur = self._conn.execute(
                "UPDATE dedup_members SET action = NULL, moved_to = NULL, acted_at = NULL "
                "WHERE group_id = ?",
                (int(group_id),),
            )
            self._conn.execute(
                "UPDATE dedup_groups SET resolved_at = NULL, keep_path = NULL WHERE id = ?",
                (int(group_id),),
            )
        return cur.rowcount

    # --- aggregates --------------------------------------------------------

    def totals(self) -> dict[str, int]:
        """Return ``{"groups": n, "reclaimable_bytes": n}`` for unresolved groups.

        ``reclaimable_bytes`` is a SQL-side estimate: per group, the total size
        of its members minus the largest single copy. The exact figure (total
        minus the *ranked* best pick) needs the ranking rules and is computed by
        ``dedup list`` / the ``/dedup`` page instead.
        """
        try:
            groups = self._conn.execute(
                "SELECT COUNT(*) FROM dedup_groups WHERE resolved_at IS NULL"
            ).fetchone()[0]
            reclaimable = self._conn.execute(
                "SELECT COALESCE(SUM(total - biggest), 0) FROM ("
                "  SELECT SUM(COALESCE(pi.file_size, 0)) AS total, "
                "         MAX(COALESCE(pi.file_size, 0)) AS biggest "
                "  FROM dedup_members dm "
                "  JOIN dedup_groups g ON g.id = dm.group_id "
                "  LEFT JOIN processed_images pi ON pi.file_path = dm.file_path "
                "  WHERE g.resolved_at IS NULL GROUP BY dm.group_id)"
            ).fetchone()[0]
        except sqlite3.OperationalError:
            # A connection opened against a pre-v13 database (or a bare test
            # connection) has no dedup tables; report nothing rather than raise.
            return {"groups": 0, "reclaimable_bytes": 0}
        return {"groups": int(groups or 0), "reclaimable_bytes": int(reclaimable or 0)}
