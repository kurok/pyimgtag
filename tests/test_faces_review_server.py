"""Tests for faces_review_server FastAPI endpoints."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from pyimgtag.faces_review_server import build_app
from pyimgtag.models import FaceDetection
from pyimgtag.progress_db import ProgressDB


@pytest.fixture()
def db(tmp_path):
    d = ProgressDB(db_path=tmp_path / "test.db")
    yield d
    d.close()


@pytest.fixture()
def client(db):
    app = build_app(db)
    return TestClient(app)


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
        faces = resp.json()
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


class TestFacesReviewServerHTML:
    def test_root_returns_html(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert b"faces" in resp.content.lower()


class TestFacesReviewServerMissingDeps:
    """Regression tests for issue #97: error messages should point at [review]."""

    def test_import_error_messages_point_to_review(self):
        """
        Regression test for issue #97:
        Verify that ImportError messages in faces_review_server.py
        point users to [review] extra, not [dev].
        """
        from pathlib import Path

        # Read the source file directly (avoids bytecode caching issues)
        source_file = Path(__file__).parent.parent / "src" / "pyimgtag" / ("faces_review_server.py")
        source = source_file.read_text()

        # Check that error messages mention [review] and not [dev]
        assert "pyimgtag[review]" in source, (
            "faces_review_server should mention 'pyimgtag[review]' in error message"
        )
        assert "pyimgtag[dev]" not in source, (
            "faces_review_server should not mention 'pyimgtag[dev]' (issue #97)"
        )

        # Ensure both fastapi and uvicorn error messages are fixed
        assert "fastapi is required for the faces review UI" in source, (
            "fastapi ImportError message should mention 'faces review UI'"
        )
        assert "uvicorn is required for the faces review UI" in source, (
            "uvicorn ImportError message should mention 'faces review UI'"
        )
