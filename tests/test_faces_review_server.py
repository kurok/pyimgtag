"""Tests for faces_review_server FastAPI endpoints.

``build_app`` and ``run_server`` only need fastapi/uvicorn, both of which are
mocked or imported directly so the success bodies run even when the heavier
test client stack (httpx) is unavailable in CI. The route-level integration
tests use Starlette's TestClient and are skipped when httpx is missing.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest.mock
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("fastapi")

from pyimgtag.faces_review_server import build_app, run_server
from pyimgtag.models import FaceDetection
from pyimgtag.progress_db import ProgressDB

_HAS_HTTPX = importlib.util.find_spec("httpx") is not None


@pytest.fixture()
def db(tmp_path):
    d = ProgressDB(db_path=tmp_path / "test.db")
    yield d
    d.close()


class TestBuildApp:
    """build_app body runs with the real (installed) fastapi."""

    def test_returns_app_with_routes(self, db):
        app = build_app(db)
        # Assert our faces router contributed paths via the OpenAPI schema
        # rather than introspecting app.routes internals: FastAPI restructures
        # route objects between releases (0.138 wraps them in a lazy
        # _IncludedRouter whose .path is None), so schema paths are stable.
        paths = set(app.openapi().get("paths", {}).keys())
        assert "/api/persons" in paths
        assert app.title == "pyimgtag Faces"


class TestRunServer:
    """run_server should build the app and hand it to uvicorn.run without
    actually binding a socket."""

    def test_invokes_uvicorn_run(self, db, capsys):
        fake_uvicorn = ModuleType("uvicorn")
        fake_uvicorn.run = MagicMock()
        with patch.dict(sys.modules, {"uvicorn": fake_uvicorn}):
            run_server(db, host="127.0.0.1", port=8766)

        fake_uvicorn.run.assert_called_once()
        # The app passed to uvicorn.run is the FastAPI instance.
        passed_app = fake_uvicorn.run.call_args[0][0]
        assert passed_app.title == "pyimgtag Faces"
        # uvicorn.run receives the host/port kwargs.
        assert fake_uvicorn.run.call_args.kwargs["host"] == "127.0.0.1"
        assert fake_uvicorn.run.call_args.kwargs["port"] == 8766
        # The startup banner is printed before the server blocks.
        assert "Face review UI: http://127.0.0.1:8766/" in capsys.readouterr().out


class TestFacesReviewServerMissingDeps:
    """Regression tests for issue #97: error messages should point at [review]."""

    def test_build_app_missing_fastapi(self, db):
        """build_app raises ImportError pointing at [review] when fastapi absent."""
        with unittest.mock.patch.dict(
            sys.modules,
            {"fastapi": None, "fastapi.responses": None, "pydantic": None},
        ):
            with pytest.raises(ImportError) as exc_info:
                build_app(db)

            msg = str(exc_info.value)
            assert "[review]" in msg, f"Error message should mention [review], got: {msg}"
            assert "[dev]" not in msg, (
                f"Error message should not mention [dev] (issue #97), got: {msg}"
            )
            assert "fastapi is required" in msg, f"Error message should mention fastapi, got: {msg}"

    def test_run_server_missing_uvicorn(self, db):
        """run_server raises ImportError pointing at [review] when uvicorn absent."""
        with unittest.mock.patch.dict(sys.modules, {"uvicorn": None}):
            with pytest.raises(ImportError) as exc_info:
                run_server(db, host="127.0.0.1", port=0)

            msg = str(exc_info.value)
            assert "[review]" in msg, f"Error message should mention [review], got: {msg}"
            assert "[dev]" not in msg, (
                f"Error message should not mention [dev] (issue #97), got: {msg}"
            )
            assert "uvicorn is required" in msg, f"Error message should mention uvicorn, got: {msg}"


# ── Route-level integration tests (require httpx for the test client) ──────────

httpx_required = pytest.mark.skipif(not _HAS_HTTPX, reason="httpx not installed")


@pytest.fixture()
def client(db):
    from fastapi.testclient import TestClient

    app = build_app(db)
    return TestClient(app)


@httpx_required
class TestFacesReviewServerPersons:
    def test_get_persons_empty(self, client):
        resp = client.get("/api/persons")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_persons_with_data(self, client, db):
        db.create_person(label="Alice", source="photos", trusted=True)
        resp = client.get("/api/persons")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["label"] == "Alice"
        assert data[0]["source"] == "photos"
        assert data[0]["trusted"] is True
        assert data[0]["face_count"] == 0

    def test_get_persons_face_count(self, client, db):
        pid = db.create_person(label="Bob")
        det = FaceDetection(
            image_path="x.jpg", bbox_x=0, bbox_y=0, bbox_w=30, bbox_h=30, confidence=0.9
        )
        fid = db.insert_face("x.jpg", det)
        db.set_person_id(fid, pid)
        resp = client.get("/api/persons")
        assert resp.json()[0]["face_count"] == 1


@httpx_required
class TestFacesReviewServerFaces:
    def test_get_faces_for_person(self, client, db):
        pid = db.create_person(label="Carol")
        det = FaceDetection(
            image_path="img.jpg", bbox_x=10, bbox_y=20, bbox_w=40, bbox_h=40, confidence=0.8
        )
        fid = db.insert_face("img.jpg", det)
        db.set_person_id(fid, pid)
        resp = client.get(f"/api/persons/{pid}/faces")
        assert resp.status_code == 200
        body = resp.json()  # paginated envelope: {"total": N, "items": [...]}
        assert body["total"] == 1
        faces = body["items"]
        assert len(faces) == 1
        assert faces[0]["id"] == fid
        assert faces[0]["image_path"] == "img.jpg"
        assert "thumb" in faces[0]  # may be None since test image doesn't exist on disk

    def test_get_faces_person_not_found(self, client):
        resp = client.get("/api/persons/9999/faces")
        assert resp.status_code == 404

    def test_unassign_face(self, client, db):
        pid = db.create_person(label="Dan")
        det = FaceDetection(
            image_path="y.jpg", bbox_x=0, bbox_y=0, bbox_w=30, bbox_h=30, confidence=0.7
        )
        fid = db.insert_face("y.jpg", det)
        db.set_person_id(fid, pid)
        resp = client.post(f"/api/faces/{fid}/unassign")
        assert resp.status_code == 200
        unassigned = db.get_unassigned_faces()
        assert any(f["id"] == fid for f in unassigned)


@httpx_required
class TestFacesReviewServerMerge:
    def test_merge_persons(self, client, db):
        p1 = db.create_person(label="Eve")
        p2 = db.create_person(label="Eve")
        det = FaceDetection(
            image_path="z.jpg", bbox_x=0, bbox_y=0, bbox_w=30, bbox_h=30, confidence=0.9
        )
        fid = db.insert_face("z.jpg", det)
        db.set_person_id(fid, p2)
        resp = client.post(f"/api/persons/{p2}/merge/{p1}")
        assert resp.status_code == 200
        persons = db.get_persons()
        assert all(p.person_id != p2 for p in persons)
        target = next(p for p in persons if p.person_id == p1)
        assert fid in target.face_ids

    def test_delete_person(self, client, db):
        pid = db.create_person(label="Frank")
        det = FaceDetection(
            image_path="w.jpg", bbox_x=0, bbox_y=0, bbox_w=30, bbox_h=30, confidence=0.9
        )
        fid = db.insert_face("w.jpg", det)
        db.set_person_id(fid, pid)
        resp = client.delete(f"/api/persons/{pid}")
        assert resp.status_code == 200
        assert all(p.person_id != pid for p in db.get_persons())
        assert any(f["id"] == fid for f in db.get_unassigned_faces())

    def test_update_person_label(self, client, db):
        pid = db.create_person(label="Grace")
        resp = client.post(f"/api/persons/{pid}/label", json={"label": "Grace H."})
        assert resp.status_code == 200
        persons = db.get_persons()
        p = next(p for p in persons if p.person_id == pid)
        assert p.label == "Grace H."


@httpx_required
class TestFacesReviewServerHTML:
    def test_root_returns_html(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert b"faces" in resp.content.lower()


@httpx_required
class TestFacesDocsDisabled:
    """Regression tests for issue #98.B — faces UI must not expose API docs."""

    def test_docs_returns_404(self, client):
        resp = client.get("/docs")
        assert resp.status_code == 404

    def test_redoc_returns_404(self, client):
        resp = client.get("/redoc")
        assert resp.status_code == 404

    def test_openapi_json_returns_404(self, client):
        resp = client.get("/openapi.json")
        assert resp.status_code == 404


@httpx_required
class TestFacesUiXss:
    """Regression test for issue #104: stored XSS in faces review UI action buttons."""

    def test_malicious_label_not_inlined_into_html(self, db, tmp_path):
        """A label containing JS injection payload must not appear in the served HTML.

        The exploit required the label to be serialised into an onclick="rename(...)"
        attribute.  The fix builds card DOM via createElement/textContent/addEventListener
        so no user data is ever placed in an HTML attribute at render time.
        """
        from fastapi.testclient import TestClient

        payload = "x',alert(document.cookie),'y"
        pid = db.create_person(label="placeholder", source="test")
        db.update_person_label(pid, payload)

        app = build_app(db)
        client = TestClient(app)
        resp = client.get("/")
        assert resp.status_code == 200
        body = resp.text

        # The inline onclick pattern must be gone entirely
        assert 'onclick="rename(' not in body, (
            'onclick="rename(" found in HTML — label is still inlined into an event handler'
        )

        # The raw payload string must not appear anywhere in the HTML
        assert "alert(document.cookie)" not in body, (
            "alert(document.cookie) found in HTML — attacker payload is inlined into the page"
        )
