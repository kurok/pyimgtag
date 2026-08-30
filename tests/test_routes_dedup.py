"""Tests for the /dedup page router."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from pyimgtag.db.dedup_db import ACTION_MOVE, ACTION_TAG  # noqa: E402
from pyimgtag.models import ImageResult  # noqa: E402
from pyimgtag.progress_db import ProgressDB  # noqa: E402
from pyimgtag.webapp.routes_dedup import build_dedup_router, render_dedup_html  # noqa: E402

HASH_A = "ffffffffffffffff"
HASH_A_NEAR = "fffffffffffffff0"


def _record(db: ProgressDB, path: Path, phash: str) -> str:
    db.mark_done(
        path,
        ImageResult(
            file_path=str(path),
            file_name=path.name,
            processing_status="ok",
            phash=phash,
        ),
    )
    return str(path)


def _seeded_db(tmp_path: Path) -> tuple[ProgressDB, str, str]:
    """DB with one group: a small loser and a big keeper."""
    db = ProgressDB(db_path=tmp_path / "progress.db")
    lib = tmp_path / "lib"
    lib.mkdir(parents=True, exist_ok=True)
    small_path = lib / "small.jpg"
    small_path.write_bytes(b"x" * 100)
    os.utime(small_path, (1_000_000, 1_000_000))
    small = _record(db, small_path, HASH_A)
    big_path = lib / "big.jpg"
    big_path.write_bytes(b"x" * 900)
    os.utime(big_path, (2_000_000, 2_000_000))
    big = _record(db, big_path, HASH_A_NEAR)
    db.replace_unresolved_dedup_groups([("duplicate", [small, big])], 5)
    return db, small, big


def _client(db, api_base="/dedup", prefix="/dedup"):
    app = FastAPI()
    app.include_router(build_dedup_router(db, api_base=api_base), prefix=prefix)
    return TestClient(app)


# --- page ------------------------------------------------------------------


def test_page_renders_with_nav_and_modal(tmp_path):
    db = ProgressDB(db_path=tmp_path / "p.db")
    client = _client(db)
    r = client.get("/dedup/")
    assert r.status_code == 200
    assert 'class="nav-link active" href="/dedup"' in r.text
    # The gated confirm modal shell + helpers come from nav.py.
    assert 'id="modal-overlay"' in r.text
    assert "function openModal(" in r.text
    assert "/dedup/api/groups" in r.text
    assert "pyimgtag dedup scan" in r.text
    assert "__" not in r.text.replace("__pycache__", "")
    db.close()


def test_render_dedup_html_has_the_quarantine_default(tmp_path):
    html = render_dedup_html("/dedup")
    assert "~/pyimgtag-duplicates" in html
    assert "/review/thumbnail" in html


def test_nav_exposes_the_dedup_link():
    from pyimgtag.webapp.nav import render_nav

    assert '<a class="nav-link" href="/dedup">Dedup</a>' in render_nav("insights")


# --- api/groups -------------------------------------------------------------


def test_api_groups_returns_the_ranked_plan(tmp_path):
    db, small, big = _seeded_db(tmp_path)
    client = _client(db)
    doc = client.get("/dedup/api/groups").json()
    assert doc["total_groups"] == 1
    group = doc["groups"][0]
    assert group["best_path"] == big
    assert group["count"] == 2
    assert group["reclaimable_bytes"] == 100
    assert group["members"][0]["is_best"] is True
    assert "size" in group["best_reasons"]
    db.close()


def test_api_groups_honours_prefer(tmp_path):
    db, small, big = _seeded_db(tmp_path)
    client = _client(db)
    doc = client.get("/dedup/api/groups?prefer=mtime").json()
    assert doc["groups"][0]["best_path"] == small
    assert doc["prefer"][0] == "mtime"
    db.close()


def test_api_groups_rejects_a_bad_prefer(tmp_path):
    db = ProgressDB(db_path=tmp_path / "p.db")
    r = _client(db).get("/dedup/api/groups?prefer=nope")
    assert r.status_code == 400
    assert "unknown ranking criterion" in r.json()["error"]
    db.close()


def test_api_groups_can_include_resolved(tmp_path):
    db, small, big = _seeded_db(tmp_path)
    group_id = db.list_dedup_groups()[0]["id"]
    db.mark_dedup_resolved(group_id, big)
    client = _client(db)
    assert client.get("/dedup/api/groups").json()["groups"] == []
    assert len(client.get("/dedup/api/groups?include_resolved=1").json()["groups"]) == 1
    db.close()


# --- api/apply --------------------------------------------------------------


def test_api_apply_moves_the_losers(tmp_path):
    db, small, big = _seeded_db(tmp_path)
    group_id = db.list_dedup_groups()[0]["id"]
    quarantine = tmp_path / "quarantine"
    client = _client(db)

    body = {"group_ids": [group_id], "move_to": str(quarantine)}
    result = client.post("/dedup/api/apply", json=body).json()
    assert result["ok"] is True
    assert result["moved"] == 1
    assert not Path(small).exists()
    assert Path(big).exists()

    group = db.get_dedup_group(group_id)
    assert group["resolved_at"] is not None
    assert group["keep_path"] == big
    loser = next(m for m in group["members"] if m["file_path"] == small)
    assert loser["action"] == ACTION_MOVE
    assert Path(loser["moved_to"]).exists()
    db.close()


def test_api_apply_honours_an_explicit_keep(tmp_path):
    db, small, big = _seeded_db(tmp_path)
    group_id = db.list_dedup_groups()[0]["id"]
    client = _client(db)
    body = {
        "group_ids": [group_id],
        "move_to": str(tmp_path / "q"),
        "keep": {str(group_id): small},
    }
    result = client.post("/dedup/api/apply", json=body).json()
    assert result["ok"] is True
    assert Path(small).exists()
    assert not Path(big).exists()
    assert db.get_dedup_group(group_id)["keep_path"] == small
    db.close()


def test_api_apply_requires_a_destination_and_groups(tmp_path):
    db, _small, _big = _seeded_db(tmp_path)
    client = _client(db)
    r = client.post("/dedup/api/apply", json={"group_ids": [1], "move_to": "  "})
    assert r.status_code == 400
    assert r.json()["error"] == "move_to_required"
    r = client.post("/dedup/api/apply", json={"group_ids": [], "move_to": "/q"})
    assert r.status_code == 400
    assert r.json()["error"] == "no_groups_selected"
    db.close()


def test_api_apply_rejects_a_keep_path_outside_the_group(tmp_path):
    db, _small, _big = _seeded_db(tmp_path)
    group_id = db.list_dedup_groups()[0]["id"]
    client = _client(db)
    body = {
        "group_ids": [group_id],
        "move_to": str(tmp_path / "q"),
        "keep": {str(group_id): "/nope.jpg"},
    }
    result = client.post("/dedup/api/apply", json=body).json()
    assert result["ok"] is False
    assert "keep path is not a member" in result["errors"][0]
    db.close()


def test_api_apply_never_moves_photos_library_originals(tmp_path):
    db = ProgressDB(db_path=tmp_path / "progress.db")
    original_path = tmp_path / "Photos.photoslibrary" / "originals" / "a.jpg"
    original_path.parent.mkdir(parents=True, exist_ok=True)
    original_path.write_bytes(b"x" * 100)
    original = _record(db, original_path, HASH_A)
    export_path = tmp_path / "export" / "a.jpg"
    export_path.parent.mkdir(parents=True, exist_ok=True)
    export_path.write_bytes(b"x" * 900)
    export = _record(db, export_path, HASH_A_NEAR)
    db.replace_unresolved_dedup_groups([("duplicate", [original, export])], 5)
    group_id = db.list_dedup_groups()[0]["id"]

    client = _client(db)
    body = {"group_ids": [group_id], "move_to": str(tmp_path / "q")}
    result = client.post("/dedup/api/apply", json=body).json()
    assert result["tagged"] == 1
    assert result["moved"] == 0
    assert Path(original).exists()
    member = next(m for m in db.get_dedup_group(group_id)["members"] if m["file_path"] == original)
    assert member["action"] == ACTION_TAG
    db.close()


def test_api_apply_skips_an_already_resolved_group(tmp_path):
    db, _small, big = _seeded_db(tmp_path)
    group_id = db.list_dedup_groups()[0]["id"]
    db.mark_dedup_resolved(group_id, big)
    client = _client(db)
    result = client.post(
        "/dedup/api/apply", json={"group_ids": [group_id], "move_to": str(tmp_path / "q")}
    ).json()
    assert result["ok"] is False
    assert "not an unresolved group" in result["errors"][0]
    db.close()


# --- api/undo ---------------------------------------------------------------


def test_api_undo_restores_the_moved_copies(tmp_path):
    db, small, big = _seeded_db(tmp_path)
    group_id = db.list_dedup_groups()[0]["id"]
    client = _client(db)
    client.post(
        "/dedup/api/apply",
        json={"group_ids": [group_id], "move_to": str(tmp_path / "quarantine")},
    )
    assert not Path(small).exists()

    result = client.post("/dedup/api/undo", json={"group_id": group_id}).json()
    assert result["ok"] is True
    assert result["restored"] == 1
    assert Path(small).exists()
    group = db.get_dedup_group(group_id)
    assert group["resolved_at"] is None
    assert all(m["action"] is None for m in group["members"])
    db.close()


def test_api_undo_unknown_group(tmp_path):
    db = ProgressDB(db_path=tmp_path / "p.db")
    r = _client(db).post("/dedup/api/undo", json={"group_id": 999})
    assert r.status_code == 404
    assert r.json()["error"] == "group_not_found"
    db.close()


def test_api_undo_keeps_the_record_when_a_file_is_gone(tmp_path):
    db, small, big = _seeded_db(tmp_path)
    group_id = db.list_dedup_groups()[0]["id"]
    db.record_dedup_action(group_id, small, ACTION_MOVE, str(tmp_path / "gone.jpg"))
    db.mark_dedup_resolved(group_id, big)
    result = _client(db).post("/dedup/api/undo", json={"group_id": group_id}).json()
    assert result["ok"] is False
    assert db.get_dedup_group(group_id)["resolved_at"] is not None
    db.close()
