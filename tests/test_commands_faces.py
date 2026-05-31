"""Tests for faces command handlers."""

from __future__ import annotations

import argparse
import errno
import sys
import threading
from unittest.mock import MagicMock, patch

from PIL import Image

from pyimgtag.commands.faces import (
    _handle_faces_apply,
    _handle_faces_cluster,
    _handle_faces_import_photos,
    _handle_faces_review,
    _handle_faces_scan,
    _start_cluster_thread,
    _write_person_keywords,
    cmd_faces,
)
from pyimgtag.models import FaceDetection
from pyimgtag.progress_db import ProgressDB
from pyimgtag.webapp.routes_review import _make_thumbnail


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
        web=False,
        no_web=True,
        web_host="127.0.0.1",
        web_port=8770,
        no_browser=True,
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
        with patch("pyimgtag.commands.faces.scan_and_store", None):
            rc = _handle_faces_scan(args)
        assert rc == 1

    def test_missing_face_recognition_extra_returns_1(self, tmp_path, capsys):
        """Regression test for issue #89: friendly error when face_recognition is missing."""
        # Create a test image file so scan_directory finds something
        img = tmp_path / "photo.jpg"
        img.write_bytes(b"\xff\xd8\xff")

        args = _make_args(input_dir=str(tmp_path), db=str(tmp_path / "test.db"))
        with patch("pyimgtag.face_detection._check_face_recognition") as mock_check:
            mock_check.side_effect = ImportError(
                "face_recognition is not installed. "
                "Install the [face] extra: pip install pyimgtag[face]"
            )
            rc = _handle_faces_scan(args)
        assert rc == 1
        captured = capsys.readouterr()
        assert "face_recognition is not installed" in captured.err
        assert "pip install pyimgtag[face]" in captured.err
        # Database should not have been created
        assert not (tmp_path / "test.db").exists()

    @patch("pyimgtag.commands.faces.scan_directory")
    def test_file_not_found_returns_1(self, mock_scan, tmp_path):
        mock_scan.side_effect = FileNotFoundError("not found")
        args = _make_args(input_dir="/no/such/dir", db=str(tmp_path / "test.db"))
        rc = _handle_faces_scan(args)
        assert rc == 1

    @patch("pyimgtag.commands.faces.start_dashboard_for", return_value=(None, None))
    @patch("pyimgtag.face_detection._check_face_recognition")
    @patch("pyimgtag.commands.faces.scan_directory")
    def test_no_files_returns_0(self, mock_scan, mock_check, mock_dash, tmp_path):
        mock_scan.return_value = []
        args = _make_args(input_dir=str(tmp_path), db=str(tmp_path / "test.db"))
        rc = _handle_faces_scan(args)
        assert rc == 0

    @patch("pyimgtag.commands.faces.start_dashboard_for", return_value=(None, None))
    @patch("pyimgtag.face_detection._check_face_recognition")
    @patch("pyimgtag.commands.faces.scan_and_store")
    @patch("pyimgtag.commands.faces.scan_directory")
    def test_scan_processes_files_with_db(
        self, mock_scan_dir, mock_store, mock_check, mock_dash, tmp_path
    ):
        img = tmp_path / "photo.jpg"
        img.write_bytes(b"\xff\xd8\xff")
        mock_scan_dir.return_value = [img]
        mock_store.return_value = 2
        args = _make_args(input_dir=str(tmp_path), db=str(tmp_path / "progress.db"))
        rc = _handle_faces_scan(args)
        assert rc == 0
        mock_check.assert_called_once()
        mock_store.assert_called_once()

    @patch("pyimgtag.commands.faces.start_dashboard_for", return_value=(None, None))
    @patch("pyimgtag.face_detection._check_face_recognition")
    @patch("pyimgtag.commands.faces.scan_and_store")
    @patch("pyimgtag.commands.faces.scan_directory")
    def test_scan_with_limit(self, mock_scan_dir, mock_store, mock_check, mock_dash, tmp_path):
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

    def _setup_person_with_face(self, tmp_path):
        db_path = tmp_path / "test.db"
        with ProgressDB(db_path=db_path) as db:
            det = FaceDetection(image_path="/img/a.jpg")
            face_id = db.insert_face("/img/a.jpg", det)
            person_id = db.create_person(label="Alice")
            db.set_person_id(face_id, person_id)
        return db_path

    def test_write_exif_failure_counted(self, tmp_path):
        db_path = self._setup_person_with_face(tmp_path)
        args = _make_args(db=str(db_path), write_exif=True)
        with patch("pyimgtag.commands.faces._write_person_keywords", return_value="write error"):
            rc = _handle_faces_apply(args)
        assert rc == 0

    def test_dry_run_prints_without_writing(self, tmp_path):
        db_path = self._setup_person_with_face(tmp_path)
        args = _make_args(db=str(db_path), dry_run=True)
        with patch("pyimgtag.commands.faces._write_person_keywords") as mock_write:
            rc = _handle_faces_apply(args)
        assert rc == 0
        mock_write.assert_not_called()


class TestWritePersonKeywords:
    def test_sidecar_only_calls_write_xmp_sidecar(self, tmp_path):
        args = _make_args(sidecar_only=True)
        with patch("pyimgtag.exif_writer.write_xmp_sidecar", return_value=None) as mock_xmp:
            err = _write_person_keywords("/img/photo.jpg", ["person:Alice"], args)
        assert err is None
        mock_xmp.assert_called_once_with("/img/photo.jpg", keywords=["person:Alice"])

    def test_unsupported_extension_falls_back_to_sidecar(self, tmp_path):
        args = _make_args(write_exif=True, sidecar_only=False)
        with patch("pyimgtag.exif_writer.write_xmp_sidecar", return_value=None) as mock_xmp:
            with patch("pyimgtag.exif_writer.SUPPORTED_DIRECT_WRITE_EXTENSIONS", set()):
                err = _write_person_keywords("/img/photo.heic", ["person:Bob"], args)
        assert err is None
        mock_xmp.assert_called_once()

    def test_supported_extension_calls_write_exif(self, tmp_path):
        args = _make_args(write_exif=True, sidecar_only=False)
        with patch("pyimgtag.exif_writer.SUPPORTED_DIRECT_WRITE_EXTENSIONS", {".jpg"}):
            with patch(
                "pyimgtag.exif_writer.write_exif_description", return_value=None
            ) as mock_exif:
                err = _write_person_keywords("/img/photo.jpg", ["person:Alice"], args)
        assert err is None
        mock_exif.assert_called_once_with("/img/photo.jpg", keywords=["person:Alice"], merge=True)


class TestMakeThumbnail:
    def test_returns_jpeg_bytes_for_valid_image(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pyimgtag.webapp.routes_review._THUMB_DIR", tmp_path / "thumbs")
        img_path = tmp_path / "test.jpg"
        img = Image.new("RGB", (50, 50), color="blue")
        img.save(str(img_path), "JPEG")

        result = _make_thumbnail(str(img_path), 32)
        assert result is not None
        assert isinstance(result, bytes)
        assert result[:2] == b"\xff\xd8"  # JPEG magic

    def test_returns_none_for_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pyimgtag.webapp.routes_review._THUMB_DIR", tmp_path / "thumbs")
        result = _make_thumbnail(str(tmp_path / "nonexistent.jpg"), 100)
        assert result is None

    def test_returns_cached_bytes_on_second_call(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pyimgtag.webapp.routes_review._THUMB_DIR", tmp_path / "thumbs")
        img_path = tmp_path / "img.jpg"
        img = Image.new("RGB", (80, 80), color="green")
        img.save(str(img_path), "JPEG")

        first = _make_thumbnail(str(img_path), 40)
        second = _make_thumbnail(str(img_path), 40)
        assert first == second


class TestCmdFacesDispatch:
    """Exercise every dispatch branch in cmd_faces."""

    def test_dispatch_scan(self):
        args = _make_args(faces_action="scan")
        with patch("pyimgtag.commands.faces._handle_faces_scan", return_value=0) as h:
            assert cmd_faces(args) == 0
        h.assert_called_once_with(args)

    def test_dispatch_cluster(self):
        args = _make_args(faces_action="cluster")
        with patch("pyimgtag.commands.faces._handle_faces_cluster", return_value=0) as h:
            assert cmd_faces(args) == 0
        h.assert_called_once_with(args)

    def test_dispatch_review(self):
        args = _make_args(faces_action="review")
        with patch("pyimgtag.commands.faces._handle_faces_review", return_value=0) as h:
            assert cmd_faces(args) == 0
        h.assert_called_once_with(args)

    def test_dispatch_apply(self):
        args = _make_args(faces_action="apply")
        with patch("pyimgtag.commands.faces._handle_faces_apply", return_value=0) as h:
            assert cmd_faces(args) == 0
        h.assert_called_once_with(args)

    def test_dispatch_import_photos(self):
        args = _make_args(faces_action="import-photos")
        with patch("pyimgtag.commands.faces._handle_faces_import_photos", return_value=0) as h:
            assert cmd_faces(args) == 0
        h.assert_called_once_with(args)

    def test_dispatch_ui(self):
        args = _make_args(faces_action="ui")
        with patch("pyimgtag.commands.faces._handle_faces_ui", return_value=0) as h:
            assert cmd_faces(args) == 0
        h.assert_called_once_with(args)


class TestHandleFacesScanPhotosLibrary:
    @patch("pyimgtag.commands.faces.start_dashboard_for", return_value=(None, None))
    @patch("pyimgtag.face_detection._check_face_recognition")
    @patch("pyimgtag.commands.faces.scan_and_store")
    @patch("pyimgtag.commands.faces.scan_photos_library")
    def test_scan_photos_library_branch(
        self, mock_scan_lib, mock_store, mock_check, mock_dash, tmp_path
    ):
        img = tmp_path / "p.jpg"
        img.write_bytes(b"\xff\xd8\xff")
        mock_scan_lib.return_value = [img]
        mock_store.return_value = 1
        args = _make_args(
            input_dir=None, photos_library="/lib.photoslibrary", db=str(tmp_path / "p.db")
        )
        rc = _handle_faces_scan(args)
        assert rc == 0
        mock_scan_lib.assert_called_once()

    @patch("pyimgtag.commands.faces.start_dashboard_for", return_value=(None, None))
    @patch("pyimgtag.face_detection._check_face_recognition")
    @patch("pyimgtag.commands.faces.scan_photos_library")
    def test_scan_photos_library_not_found(
        self, mock_scan_lib, mock_check, mock_dash, tmp_path, capsys
    ):
        mock_scan_lib.side_effect = FileNotFoundError("no library")
        args = _make_args(input_dir=None, photos_library="/missing", db=str(tmp_path / "p.db"))
        rc = _handle_faces_scan(args)
        assert rc == 1
        assert "no library" in capsys.readouterr().err


class TestHandleFacesScanErrors:
    @patch("pyimgtag.commands.faces.start_dashboard_for", return_value=(None, None))
    @patch("pyimgtag.face_detection._check_face_recognition")
    @patch("pyimgtag.commands.faces.scan_and_store")
    @patch("pyimgtag.commands.faces.scan_directory")
    def test_enospc_aborts_scan(
        self, mock_scan_dir, mock_store, mock_check, mock_dash, tmp_path, capsys
    ):
        img = tmp_path / "p.jpg"
        img.write_bytes(b"\xff\xd8\xff")
        mock_scan_dir.return_value = [img]
        exc = OSError("disk full")
        exc.errno = errno.ENOSPC
        mock_store.side_effect = exc
        args = _make_args(input_dir=str(tmp_path), db=str(tmp_path / "p.db"))
        rc = _handle_faces_scan(args)
        assert rc == 1  # interrupted -> non-zero
        assert "disk full" in capsys.readouterr().err

    @patch("pyimgtag.commands.faces.start_dashboard_for", return_value=(None, None))
    @patch("pyimgtag.face_detection._check_face_recognition")
    @patch("pyimgtag.commands.faces.scan_and_store")
    @patch("pyimgtag.commands.faces.scan_directory")
    def test_generic_oserror_skips_file(
        self, mock_scan_dir, mock_store, mock_check, mock_dash, tmp_path, capsys
    ):
        imgs = [tmp_path / f"p{i}.jpg" for i in range(2)]
        for f in imgs:
            f.write_bytes(b"\xff\xd8\xff")
        mock_scan_dir.return_value = imgs
        bad = OSError("permission denied")
        bad.errno = errno.EACCES
        mock_store.side_effect = [bad, 1]
        args = _make_args(input_dir=str(tmp_path), db=str(tmp_path / "p.db"))
        rc = _handle_faces_scan(args)
        assert rc == 0
        err = capsys.readouterr().err
        assert "skipped" in err
        assert "1 error(s) skipped" in err

    @patch("pyimgtag.commands.faces.start_dashboard_for", return_value=(None, None))
    @patch("pyimgtag.face_detection._check_face_recognition")
    @patch("pyimgtag.commands.faces.scan_and_store")
    @patch("pyimgtag.commands.faces.scan_directory")
    def test_generic_exception_skips_file(
        self, mock_scan_dir, mock_store, mock_check, mock_dash, tmp_path, capsys
    ):
        img = tmp_path / "p.jpg"
        img.write_bytes(b"\xff\xd8\xff")
        mock_scan_dir.return_value = [img]
        mock_store.side_effect = ValueError("corrupt image")
        args = _make_args(input_dir=str(tmp_path), db=str(tmp_path / "p.db"))
        rc = _handle_faces_scan(args)
        assert rc == 0
        err = capsys.readouterr().err
        assert "corrupt image" in err
        assert "1 error(s) skipped" in err

    @patch("pyimgtag.commands.faces.start_dashboard_for")
    @patch("pyimgtag.face_detection._check_face_recognition")
    @patch("pyimgtag.commands.faces.scan_and_store")
    @patch("pyimgtag.commands.faces.scan_directory")
    def test_keyboard_interrupt_marks_session_and_stops_dashboard(
        self, mock_scan_dir, mock_store, mock_check, mock_dash, tmp_path, capsys
    ):
        img = tmp_path / "p.jpg"
        img.write_bytes(b"\xff\xd8\xff")
        mock_scan_dir.return_value = [img]
        mock_store.side_effect = KeyboardInterrupt()
        session = MagicMock()
        dashboard = MagicMock()
        mock_dash.return_value = (session, dashboard)
        args = _make_args(input_dir=str(tmp_path), db=str(tmp_path / "p.db"))
        rc = _handle_faces_scan(args)
        assert rc == 1
        session.mark_interrupted.assert_called_once()
        dashboard.stop.assert_called_once()
        assert "Interrupted." in capsys.readouterr().err

    @patch("pyimgtag.commands.faces.start_dashboard_for")
    @patch("pyimgtag.face_detection._check_face_recognition")
    @patch("pyimgtag.commands.faces.scan_and_store")
    @patch("pyimgtag.commands.faces.scan_directory")
    def test_session_records_and_completes_with_dashboard(
        self, mock_scan_dir, mock_store, mock_check, mock_dash, tmp_path
    ):
        img = tmp_path / "p.jpg"
        img.write_bytes(b"\xff\xd8\xff")
        mock_scan_dir.return_value = [img]
        mock_store.return_value = 3
        session = MagicMock()
        dashboard = MagicMock()
        mock_dash.return_value = (session, dashboard)
        args = _make_args(input_dir=str(tmp_path), db=str(tmp_path / "p.db"))
        rc = _handle_faces_scan(args)
        assert rc == 0
        session.set_counter.assert_any_call("scanned_total", 1)
        session.mark_running.assert_called_once()
        session.record_item.assert_called_once()
        session.mark_completed.assert_called_once()
        dashboard.stop.assert_called_once()


class TestStartClusterThread:
    def test_import_error_returns_noop_thread(self, tmp_path):
        db = MagicMock()
        db.path = str(tmp_path / "p.db")
        args = _make_args()
        stop_event = threading.Event()
        with patch.dict(sys.modules, {"pyimgtag.face_clustering": None}):
            t = _start_cluster_thread(db, args, stop_event)
        assert isinstance(t, threading.Thread)
        t.join(timeout=2.0)
        assert not t.is_alive()

    def test_loop_calls_recluster_then_final_pass(self, tmp_path):
        """Drive the loop body: one timed pass plus the final pass."""
        db_path = tmp_path / "p.db"
        with ProgressDB(db_path=db_path):
            pass
        db = MagicMock()
        db.path = str(db_path)
        args = _make_args(eps=0.4, min_samples=3)
        stop_event = threading.Event()

        recluster = MagicMock()
        # First wait() returns False (run loop body once), then True (exit loop)
        with patch("pyimgtag.commands.faces._CLUSTER_INTERVAL_S", 0.01):
            with patch("pyimgtag.face_clustering.recluster_auto", recluster):
                t = _start_cluster_thread(db, args, stop_event)
                # let the loop run at least one iteration
                import time as _t

                _t.sleep(0.05)
                stop_event.set()
                t.join(timeout=2.0)
        assert not t.is_alive()
        assert recluster.call_count >= 1
        # eps/min_samples threaded through
        _, kwargs = recluster.call_args
        assert kwargs["eps"] == 0.4
        assert kwargs["min_samples"] == 3

    def test_loop_swallows_recluster_exceptions(self, tmp_path):
        """A failing recluster must not crash the thread (covers both except blocks)."""
        db_path = tmp_path / "p.db"
        with ProgressDB(db_path=db_path):
            pass
        db = MagicMock()
        db.path = str(db_path)
        args = _make_args()
        stop_event = threading.Event()

        recluster = MagicMock(side_effect=RuntimeError("boom"))
        with patch("pyimgtag.commands.faces._CLUSTER_INTERVAL_S", 0.01):
            with patch("pyimgtag.face_clustering.recluster_auto", recluster):
                t = _start_cluster_thread(db, args, stop_event)
                import time as _t

                _t.sleep(0.05)
                stop_event.set()
                t.join(timeout=2.0)
        assert not t.is_alive()
        assert recluster.call_count >= 1


class TestHandleFacesReview:
    def test_no_faces_returns_0(self, tmp_path, capsys):
        db_path = tmp_path / "p.db"
        with ProgressDB(db_path=db_path):
            pass
        args = _make_args(db=str(db_path))
        rc = _handle_faces_review(args)
        assert rc == 0
        assert "No faces detected yet" in capsys.readouterr().err

    def test_reports_persons_and_unassigned(self, tmp_path, capsys):
        db_path = tmp_path / "p.db"
        with ProgressDB(db_path=db_path) as db:
            # one assigned face on a confirmed/labelled person
            det = FaceDetection(image_path="/img/a.jpg")
            fid = db.insert_face("/img/a.jpg", det)
            pid = db.create_person(label="Alice", confirmed=True)
            db.set_person_id(fid, pid)
            # one unassigned face
            det2 = FaceDetection(image_path="/img/b.jpg")
            db.insert_face("/img/b.jpg", det2)
        args = _make_args(db=str(db_path))
        rc = _handle_faces_review(args)
        assert rc == 0
        err = capsys.readouterr().err
        assert "Faces: 2 total" in err
        assert "Alice" in err
        assert "not assigned to any person" in err
        assert "faces cluster" in err

    def test_reports_unlabelled_auto_person_no_unassigned(self, tmp_path, capsys):
        db_path = tmp_path / "p.db"
        with ProgressDB(db_path=db_path) as db:
            det = FaceDetection(image_path="/img/a.jpg")
            fid = db.insert_face("/img/a.jpg", det)
            pid = db.create_person(label="", confirmed=False)
            db.set_person_id(fid, pid)
        args = _make_args(db=str(db_path))
        rc = _handle_faces_review(args)
        assert rc == 0
        err = capsys.readouterr().err
        assert "auto" in err
        assert "unlabelled" in err
        assert "not assigned" not in err


class TestHandleFacesApplyMore:
    def _setup_person_with_face(self, tmp_path, label="Alice", image="/img/a.jpg"):
        db_path = tmp_path / "test.db"
        with ProgressDB(db_path=db_path) as db:
            det = FaceDetection(image_path=image)
            face_id = db.insert_face(image, det)
            person_id = db.create_person(label=label)
            db.set_person_id(face_id, person_id)
        return db_path

    def test_no_write_flags_lists_only(self, tmp_path, capsys):
        db_path = self._setup_person_with_face(tmp_path)
        args = _make_args(db=str(db_path), write_exif=False, sidecar_only=False, dry_run=False)
        rc = _handle_faces_apply(args)
        assert rc == 0
        err = capsys.readouterr().err
        assert "have person keywords" in err
        assert "--write-exif or --sidecar-only" in err

    def test_write_exif_success_counts_written(self, tmp_path, capsys):
        db_path = self._setup_person_with_face(tmp_path)
        args = _make_args(db=str(db_path), write_exif=True)
        with patch("pyimgtag.commands.faces._write_person_keywords", return_value=None):
            rc = _handle_faces_apply(args)
        assert rc == 0
        err = capsys.readouterr().err
        assert "Wrote person keywords to 1/1" in err

    def test_dry_run_summary_message(self, tmp_path, capsys):
        db_path = self._setup_person_with_face(tmp_path)
        args = _make_args(db=str(db_path), dry_run=True)
        rc = _handle_faces_apply(args)
        assert rc == 0
        err = capsys.readouterr().err
        assert "[dry-run]" in err
        assert "Would write to 1 image(s)" in err

    def test_unlabelled_person_uses_person_id_fallback(self, tmp_path, capsys):
        db_path = self._setup_person_with_face(tmp_path, label="")
        args = _make_args(db=str(db_path), write_exif=False, sidecar_only=False)
        rc = _handle_faces_apply(args)
        assert rc == 0
        assert "person_" in capsys.readouterr().err


class TestHandleFacesImportPhotos:
    def test_import_error_returns_1(self, tmp_path, capsys):
        args = _make_args(db=str(tmp_path / "p.db"))
        with patch.dict(sys.modules, {"pyimgtag.photos_faces_importer": None}):
            rc = _handle_faces_import_photos(args)
        assert rc == 1

    def test_success_reports_counts(self, tmp_path, capsys):
        db_path = tmp_path / "p.db"
        with ProgressDB(db_path=db_path):
            pass
        args = _make_args(db=str(db_path))
        with patch(
            "pyimgtag.photos_faces_importer.import_photos_persons", return_value=(5, 2)
        ) as mock_imp:
            rc = _handle_faces_import_photos(args)
        assert rc == 0
        mock_imp.assert_called_once()
        err = capsys.readouterr().err
        assert "Imported 5 person(s)" in err
        assert "2 multi-face photo(s)" in err

    def test_success_no_skipped(self, tmp_path, capsys):
        db_path = tmp_path / "p.db"
        with ProgressDB(db_path=db_path):
            pass
        args = _make_args(db=str(db_path))
        with patch("pyimgtag.photos_faces_importer.import_photos_persons", return_value=(3, 0)):
            rc = _handle_faces_import_photos(args)
        assert rc == 0
        err = capsys.readouterr().err
        assert "Imported 3 person(s)" in err
        assert "multi-face" not in err

    def test_runtime_error_returns_1(self, tmp_path, capsys):
        db_path = tmp_path / "p.db"
        with ProgressDB(db_path=db_path):
            pass
        args = _make_args(db=str(db_path))
        with patch(
            "pyimgtag.photos_faces_importer.import_photos_persons",
            side_effect=RuntimeError("photos not available"),
        ):
            rc = _handle_faces_import_photos(args)
        assert rc == 1
        assert "photos not available" in capsys.readouterr().err

    def test_keyboard_interrupt_returns_130(self, tmp_path, capsys):
        db_path = tmp_path / "p.db"
        with ProgressDB(db_path=db_path):
            pass
        args = _make_args(db=str(db_path))
        with patch(
            "pyimgtag.photos_faces_importer.import_photos_persons",
            side_effect=KeyboardInterrupt(),
        ):
            rc = _handle_faces_import_photos(args)
        assert rc == 130
        assert "Aborted by user" in capsys.readouterr().err


class TestModuleImportFallback:
    def test_scan_and_store_none_when_face_embedding_missing(self):
        """Reload commands.faces with face_embedding import forced to fail.

        Covers the module-level ``except ImportError: scan_and_store = None``
        fallback that fires in CI when the [face] extra is not installed.
        """
        import builtins
        import importlib

        import pyimgtag.commands.faces as faces_mod

        real_import = builtins.__import__

        def fake_import(name, *a, **kw):
            if name == "pyimgtag.face_embedding" or name.startswith("pyimgtag.face_embedding"):
                raise ImportError("no face_embedding without [face] extra")
            return real_import(name, *a, **kw)

        try:
            with patch.object(builtins, "__import__", fake_import):
                importlib.reload(faces_mod)
            assert faces_mod.scan_and_store is None
        finally:
            # Restore the real module state for other tests/parallel workers.
            importlib.reload(faces_mod)
        assert faces_mod.scan_and_store is not None
