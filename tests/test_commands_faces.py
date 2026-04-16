"""Tests for faces command handlers."""

from __future__ import annotations

import argparse
import sys
from unittest.mock import patch

from PIL import Image

from pyimgtag.commands.faces import (
    _handle_faces_apply,
    _handle_faces_cluster,
    _handle_faces_scan,
    cmd_faces,
)
from pyimgtag.progress_db import ProgressDB
from pyimgtag.review_server import _make_thumbnail


def _make_args(**kwargs) -> argparse.Namespace:
    defaults = dict(
        db=None,
        input_dir=None,
        photos_library=None,
        extensions="jpg,jpeg",
        limit=None,
        max_dim=800,
        detection_model="hog",
        eps=0.5,
        min_samples=2,
        dry_run=False,
        write_exif=False,
        sidecar_only=False,
        faces_action=None,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


class TestCmdFaces:
    def test_no_action_returns_1(self, tmp_path):
        args = _make_args(faces_action=None)
        assert cmd_faces(args) == 1

    def test_unknown_action_returns_1(self, tmp_path):
        args = _make_args(faces_action="unknown")
        assert cmd_faces(args) == 1


class TestHandleFacesScan:
    def test_import_error_returns_1(self, tmp_path):
        args = _make_args(input_dir=str(tmp_path), db=str(tmp_path / "test.db"))
        with patch.dict(sys.modules, {"pyimgtag.face_embedding": None}):
            rc = _handle_faces_scan(args)
        assert rc == 1

    @patch("pyimgtag.commands.faces.scan_directory")
    def test_file_not_found_returns_1(self, mock_scan, tmp_path):
        mock_scan.side_effect = FileNotFoundError("not found")
        args = _make_args(input_dir="/no/such/dir", db=str(tmp_path / "test.db"))
        rc = _handle_faces_scan(args)
        assert rc == 1

    @patch("pyimgtag.commands.faces.scan_directory")
    def test_no_files_returns_0(self, mock_scan, tmp_path):
        mock_scan.return_value = []
        args = _make_args(input_dir=str(tmp_path), db=str(tmp_path / "test.db"))
        rc = _handle_faces_scan(args)
        assert rc == 0

    @patch("pyimgtag.face_embedding.scan_and_store")
    @patch("pyimgtag.commands.faces.scan_directory")
    def test_scan_processes_files_with_db(self, mock_scan_dir, mock_store, tmp_path):
        img = tmp_path / "photo.jpg"
        img.write_bytes(b"\xff\xd8\xff")
        mock_scan_dir.return_value = [img]
        mock_store.return_value = 2
        args = _make_args(input_dir=str(tmp_path), db=str(tmp_path / "progress.db"))
        rc = _handle_faces_scan(args)
        assert rc == 0
        mock_store.assert_called_once()

    @patch("pyimgtag.face_embedding.scan_and_store")
    @patch("pyimgtag.commands.faces.scan_directory")
    def test_scan_with_limit(self, mock_scan_dir, mock_store, tmp_path):
        imgs = [tmp_path / f"p{i}.jpg" for i in range(3)]
        for f in imgs:
            f.write_bytes(b"\xff\xd8\xff")
        mock_scan_dir.return_value = imgs
        mock_store.return_value = 0
        args = _make_args(input_dir=str(tmp_path), db=str(tmp_path / "progress.db"), limit=1)
        rc = _handle_faces_scan(args)
        assert rc == 0
        assert mock_store.call_count == 1


class TestHandleFacesCluster:
    def test_import_error_returns_1(self, tmp_path):
        args = _make_args(db=str(tmp_path / "test.db"))
        with patch.dict(sys.modules, {"pyimgtag.face_clustering": None}):
            rc = _handle_faces_cluster(args)
        assert rc == 1

    @patch("pyimgtag.face_clustering.cluster_faces")
    def test_no_clusters_prints_message(self, mock_cluster, tmp_path):
        mock_cluster.return_value = {}
        args = _make_args(db=str(tmp_path / "test.db"))
        rc = _handle_faces_cluster(args)
        assert rc == 0
        mock_cluster.assert_called_once()

    @patch("pyimgtag.face_clustering.cluster_faces")
    def test_with_clusters_reports_counts(self, mock_cluster, tmp_path):
        mock_cluster.return_value = {1: [10, 11], 2: [12]}
        args = _make_args(db=str(tmp_path / "test.db"))
        rc = _handle_faces_cluster(args)
        assert rc == 0


class TestHandleFacesApply:
    def test_no_persons_returns_0(self, tmp_path):
        db_path = tmp_path / "test.db"
        with ProgressDB(db_path=db_path):
            pass
        args = _make_args(db=str(db_path))
        rc = _handle_faces_apply(args)
        assert rc == 0

    def test_persons_but_no_face_assignments_returns_0(self, tmp_path):
        db_path = tmp_path / "test.db"
        with ProgressDB(db_path=db_path) as db:
            db.create_person(label="Alice")
        args = _make_args(db=str(db_path))
        rc = _handle_faces_apply(args)
        assert rc == 0


class TestMakeThumbnail:
    def test_returns_jpeg_bytes_for_valid_image(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pyimgtag.review_server._THUMB_DIR", tmp_path / "thumbs")
        img_path = tmp_path / "test.jpg"
        img = Image.new("RGB", (50, 50), color="blue")
        img.save(str(img_path), "JPEG")

        result = _make_thumbnail(str(img_path), 32)
        assert result is not None
        assert isinstance(result, bytes)
        assert result[:2] == b"\xff\xd8"  # JPEG magic

    def test_returns_none_for_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pyimgtag.review_server._THUMB_DIR", tmp_path / "thumbs")
        result = _make_thumbnail(str(tmp_path / "nonexistent.jpg"), 100)
        assert result is None

    def test_returns_cached_bytes_on_second_call(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pyimgtag.review_server._THUMB_DIR", tmp_path / "thumbs")
        img_path = tmp_path / "img.jpg"
        img = Image.new("RGB", (80, 80), color="green")
        img.save(str(img_path), "JPEG")

        first = _make_thumbnail(str(img_path), 40)
        second = _make_thumbnail(str(img_path), 40)
        assert first == second
