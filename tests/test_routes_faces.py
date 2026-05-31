"""Tests for the extracted faces router at a non-root prefix."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from pyimgtag.progress_db import ProgressDB  # noqa: E402
from pyimgtag.webapp.routes_faces import build_faces_router  # noqa: E402


def test_faces_router_mounted_at_prefix(tmp_path):
    db = ProgressDB(db_path=tmp_path / "progress.db")
    app = FastAPI()
    app.include_router(build_faces_router(db, api_base="/faces"), prefix="/faces")

    client = TestClient(app)
    r = client.get("/faces/")
    assert r.status_code == 200
    assert "/faces/api/persons" in r.text  # HTML uses prefixed URLs

    r = client.get("/faces/api/persons")
    assert r.status_code == 200
    assert r.json() == []


def test_faces_router_mounted_at_root(tmp_path):
    db = ProgressDB(db_path=tmp_path / "progress.db")
    app = FastAPI()
    app.include_router(build_faces_router(db, api_base=""))

    client = TestClient(app)
    r = client.get("/api/persons")
    assert r.status_code == 200
    assert r.json() == []


def test_with_faces_empty_db(tmp_path):
    db = ProgressDB(db_path=tmp_path / "progress.db")
    app = FastAPI()
    app.include_router(build_faces_router(db, api_base=""))

    with TestClient(app) as client:
        r = client.get("/api/persons/with-faces")
    assert r.status_code == 200
    body = r.json()
    assert body == {"total": 0, "items": []}


def test_person_detail_existing_renders_html(tmp_path):
    import sqlite3

    db_path = tmp_path / "progress.db"
    db = ProgressDB(db_path=db_path)
    app = FastAPI()
    app.include_router(build_faces_router(db, api_base="/faces"), prefix="/faces")

    con = sqlite3.connect(str(db_path))
    cur = con.execute(
        "INSERT INTO persons (label, confirmed, source, trusted) VALUES ('Alice',1,'auto',1)"
    )
    pid = cur.lastrowid
    con.commit()
    con.close()

    client = TestClient(app)
    r = client.get(f"/faces/persons/{pid}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")


def test_person_detail_missing_redirects_to_list(tmp_path):
    # A stale card link (person deleted or re-clustered away after the grid
    # rendered) must bounce back to the faces grid, not dump a raw
    # "Person not found" JSON body to the browser.
    db = ProgressDB(db_path=tmp_path / "progress.db")
    app = FastAPI()
    app.include_router(build_faces_router(db, api_base="/faces"), prefix="/faces")

    client = TestClient(app)
    r = client.get("/faces/persons/160765", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/faces/"

    # Following the redirect lands on the faces grid (200 HTML), not a 404.
    r2 = client.get("/faces/persons/160765")
    assert r2.status_code == 200
    assert "/faces/api/persons" in r2.text


def test_with_faces_pagination(tmp_path):
    """Offset and limit are respected; total reflects the full count."""
    import sqlite3

    db_path = tmp_path / "progress.db"
    db = ProgressDB(db_path=db_path)
    app = FastAPI()
    app.include_router(build_faces_router(db, api_base=""))

    # Seed 12 persons directly so we can test pagination without face thumbnails.
    con = sqlite3.connect(str(db_path))
    for i in range(12):
        con.execute(
            "INSERT INTO persons (label, confirmed, source, trusted) VALUES (?,0,'auto',1)",
            (f"Person {i}",),
        )
    con.commit()
    con.close()

    with TestClient(app) as client:
        r = client.get("/api/persons/with-faces?offset=0&limit=10")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 12
        assert len(body["items"]) == 10

        r2 = client.get("/api/persons/with-faces?offset=10&limit=10")
        assert r2.status_code == 200
        body2 = r2.json()
        assert body2["total"] == 12
        assert len(body2["items"]) == 2


def _seed_persons_with_faces(db_path, specs):
    """Seed persons with the given (label, trusted, face_count) specs.

    Returns the list of created person ids in spec order.
    """
    import sqlite3

    con = sqlite3.connect(str(db_path))
    pids = []
    for label, trusted, n_faces in specs:
        cur = con.execute(
            "INSERT INTO persons (label, confirmed, source, trusted) VALUES (?,0,'auto',?)",
            (label, 1 if trusted else 0),
        )
        pid = cur.lastrowid
        pids.append(pid)
        for i in range(n_faces):
            con.execute(
                "INSERT INTO faces (image_path, person_id, confidence) VALUES (?,?,1.0)",
                (f"/nonexistent/{pid}_{i}.jpg", pid),
            )
    con.commit()
    con.close()
    return pids


def test_with_faces_sort_by_count(tmp_path):
    db_path = tmp_path / "progress.db"
    db = ProgressDB(db_path=db_path)
    app = FastAPI()
    app.include_router(build_faces_router(db, api_base=""))
    # Persons with 1, 3, and 2 faces respectively.
    _seed_persons_with_faces(db_path, [("one", False, 1), ("three", False, 3), ("two", False, 2)])

    with TestClient(app) as client:
        desc = client.get("/api/persons/with-faces?sort=count_desc").json()
        assert [it["face_count"] for it in desc["items"]] == [3, 2, 1]

        asc = client.get("/api/persons/with-faces?sort=count_asc").json()
        assert [it["face_count"] for it in asc["items"]] == [1, 2, 3]

        # Sort is applied across the whole set before pagination.
        page1 = client.get("/api/persons/with-faces?sort=count_desc&offset=0&limit=2").json()
        assert [it["face_count"] for it in page1["items"]] == [3, 2]
        assert page1["total"] == 3


def test_with_faces_sort_by_name(tmp_path):
    db_path = tmp_path / "progress.db"
    db = ProgressDB(db_path=db_path)
    app = FastAPI()
    app.include_router(build_faces_router(db, api_base=""))
    _seed_persons_with_faces(db_path, [("Charlie", True, 0), ("alice", True, 0), ("Bob", True, 0)])

    with TestClient(app) as client:
        names = [
            it["label"]
            for it in client.get("/api/persons/with-faces?sort=name_asc").json()["items"]
        ]
    assert names == ["alice", "Bob", "Charlie"]  # case-insensitive A-Z


def test_confirm_batch(tmp_path):
    db_path = tmp_path / "progress.db"
    db = ProgressDB(db_path=db_path)
    app = FastAPI()
    app.include_router(build_faces_router(db, api_base=""))
    pids = _seed_persons_with_faces(db_path, [("a", False, 1), ("b", False, 1), ("c", False, 1)])

    with TestClient(app) as client:
        r = client.post("/api/persons/confirm-batch", json={"person_ids": pids[:2]})
        assert r.status_code == 200
        assert r.json() == {"ok": True, "confirmed": 2}

    persons = {p.person_id: p for p in db.get_persons()}
    assert persons[pids[0]].confirmed and persons[pids[0]].trusted
    assert persons[pids[1]].confirmed and persons[pids[1]].trusted
    assert not persons[pids[2]].confirmed  # untouched


def test_delete_batch(tmp_path):
    db_path = tmp_path / "progress.db"
    db = ProgressDB(db_path=db_path)
    app = FastAPI()
    app.include_router(build_faces_router(db, api_base=""))
    pids = _seed_persons_with_faces(db_path, [("a", False, 2), ("b", False, 1), ("c", False, 1)])

    with TestClient(app) as client:
        r = client.post("/api/persons/delete-batch", json={"person_ids": pids[:2]})
        assert r.status_code == 200
        assert r.json() == {"ok": True, "deleted": 2}

    remaining = {p.person_id for p in db.get_persons()}
    assert pids[0] not in remaining and pids[1] not in remaining
    assert pids[2] in remaining
    # Faces of a deleted person are unassigned, not removed.
    assert db.get_faces_for_person(pids[0]) == []


def test_batch_empty_list_is_noop(tmp_path):
    db_path = tmp_path / "progress.db"
    db = ProgressDB(db_path=db_path)
    app = FastAPI()
    app.include_router(build_faces_router(db, api_base=""))

    with TestClient(app) as client:
        assert client.post("/api/persons/confirm-batch", json={"person_ids": []}).json() == {
            "ok": True,
            "confirmed": 0,
        }
        assert client.post("/api/persons/delete-batch", json={"person_ids": []}).json() == {
            "ok": True,
            "deleted": 0,
        }


def test_faces_grid_html_has_sort_and_bulk_controls(tmp_path):
    db = ProgressDB(db_path=tmp_path / "progress.db")
    app = FastAPI()
    app.include_router(build_faces_router(db, api_base="/faces"), prefix="/faces")

    html = TestClient(app).get("/faces/").text
    # Sort control
    assert 'id="person-sort"' in html
    assert 'value="count_desc"' in html and 'value="count_asc"' in html
    # Bulk-action bar + handlers
    assert 'id="persons-bulk-bar"' in html
    assert "confirmSelectedPersons" in html and "deleteSelectedPersons" in html
    assert "/api/persons/confirm-batch" in html and "/api/persons/delete-batch" in html
    # No unreplaced template tokens leaked
    import re

    assert not re.findall(r"__[A-Z][A-Z0-9_]+__", html)


def _seed_unassigned_faces(db_path, n):
    """Insert n faces with no person and return their ids."""
    import sqlite3

    con = sqlite3.connect(str(db_path))
    fids = []
    for i in range(n):
        cur = con.execute(
            "INSERT INTO faces (image_path, confidence) VALUES (?, 1.0)",
            (f"/nonexistent/face_{i}.jpg",),
        )
        fids.append(cur.lastrowid)
    con.commit()
    con.close()
    return fids


def test_assign_batch_new_person_from_selected(tmp_path):
    # Regression: this path used to 422 because the function-local pydantic
    # body model did not resolve under `from __future__ import annotations`.
    db_path = tmp_path / "progress.db"
    db = ProgressDB(db_path=db_path)
    app = FastAPI()
    app.include_router(build_faces_router(db, api_base=""))
    fids = _seed_unassigned_faces(db_path, 3)

    with TestClient(app) as client:
        r = client.post(
            "/api/faces/assign-batch",
            json={"face_ids": fids[:2], "person_id": None, "label": "Alice"},
        )
        assert r.status_code == 200, r.text
        new_pid = r.json()["person_id"]

    assigned = {f["id"] for f in db.get_faces_for_person(new_pid)}
    assert assigned == set(fids[:2])
    person = {p.person_id: p for p in db.get_persons()}[new_pid]
    assert person.label == "Alice" and person.trusted


def test_assign_batch_to_existing_person(tmp_path):
    import sqlite3

    db_path = tmp_path / "progress.db"
    db = ProgressDB(db_path=db_path)
    app = FastAPI()
    app.include_router(build_faces_router(db, api_base=""))
    con = sqlite3.connect(str(db_path))
    pid = con.execute(
        "INSERT INTO persons (label, confirmed, source, trusted) VALUES ('Bob',1,'auto',1)"
    ).lastrowid
    con.commit()
    con.close()
    fids = _seed_unassigned_faces(db_path, 2)

    with TestClient(app) as client:
        r = client.post("/api/faces/assign-batch", json={"face_ids": fids, "person_id": pid})
        assert r.status_code == 200, r.text
        assert r.json()["person_id"] == pid

    assert {f["id"] for f in db.get_faces_for_person(pid)} == set(fids)


def test_assign_batch_empty_returns_400(tmp_path):
    db = ProgressDB(db_path=tmp_path / "progress.db")
    app = FastAPI()
    app.include_router(build_faces_router(db, api_base=""))
    with TestClient(app) as client:
        r = client.post("/api/faces/assign-batch", json={"face_ids": []})
    assert r.status_code == 400
