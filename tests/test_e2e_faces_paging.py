"""End to end: the faces endpoints page and count in SQL (#400).

Seeds a database, starts the real dashboard (``python -m pyimgtag.webapp``,
see ``tests/e2e_support.py``) and walks every page of each list over HTTP:
the pages join up to the full list in the documented order and ``total``
matches.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import requests

from pyimgtag.progress_db import ProgressDB
from tests.e2e_support import dashboard

pytest.importorskip("uvicorn")


def _seed(db_path: Path) -> int:
    with ProgressDB(db_path) as db:
        conn = db._conn
        conn.execute("INSERT INTO persons (id, label, trusted) VALUES (1, 'Ann', 1)")
        rows = [(1, 0, c) for c in (0.2, 0.9, 0.5, 0.9, 0.1, 0.7, 0.3)]
        rows += [(None, 0, c) for c in (0.4, 0.8, 0.6, 0.4, 0.95)]
        rows += [(None, 1, c) for c in (0.5, 0.1, 0.3)]
        conn.executemany(
            "INSERT INTO faces (image_path, person_id, ignored, confidence) VALUES (?, ?, ?, ?)",
            [(f"/missing/{i}.jpg", pid, ign, conf) for i, (pid, ign, conf) in enumerate(rows)],
        )
        conn.commit()
    return 1


def _walk(base: str, path: str, page_size: int) -> tuple[int, list[dict]]:
    items: list[dict] = []
    offset = 0
    while True:
        body = requests.get(
            f"{base}{path}", params={"offset": offset, "limit": page_size}, timeout=10
        ).json()
        items += body["items"]
        offset += page_size
        if offset >= body["total"]:
            return body["total"], items


def test_every_list_pages_to_the_full_ordered_list(tmp_path: Path) -> None:
    db_path = tmp_path / "progress.db"
    pid = _seed(db_path)
    with dashboard(db_path, tmp_path) as base:
        total, faces = _walk(base, f"/faces/api/persons/{pid}/faces", 3)
        assert total == 7 == len(faces)
        confidences = [f["confidence"] for f in faces]
        assert confidences == sorted(confidences, reverse=True)
        tied = [f["id"] for f in faces if f["confidence"] == 0.9]
        assert tied == sorted(tied)  # ties keep id order across page boundaries

        total, unassigned = _walk(base, "/faces/api/faces/unassigned", 2)
        assert total == 5 and [f["id"] for f in unassigned] == sorted(f["id"] for f in unassigned)

        total, ignored = _walk(base, "/faces/api/faces/ignored", 2)
        assert total == 3 == len(ignored)

        person = requests.get(f"{base}/faces/api/persons/{pid}", timeout=10).json()
        assert person["face_count"] == 7

        assign = requests.post(
            f"{base}/faces/api/faces/assign-batch",
            json={"face_ids": [f["id"] for f in unassigned], "person_id": pid},
            timeout=10,
        )
        assert assign.json() == {"ok": True, "person_id": pid}
        total, _ = _walk(base, "/faces/api/faces/unassigned", 2)
        assert total == 0
