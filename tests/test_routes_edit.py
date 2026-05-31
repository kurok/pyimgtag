"""Tests for routes_edit error-path branches."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")

from unittest.mock import MagicMock, patch  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from pyimgtag.models import ImageResult  # noqa: E402
from pyimgtag.progress_db import ProgressDB  # noqa: E402
from pyimgtag.webapp import routes_edit  # noqa: E402
from pyimgtag.webapp.routes_edit import (  # noqa: E402
    _categorise_applescript_error,
    _Job,
    _reset_job_for_tests,
    _run_drift_prune_job,
    _run_job,
    _snapshot,
    build_edit_router,
    render_edit_html,
)


class TestRunJobDbError:
    """_run_job DB error path (lines 159-166)."""

    def test_db_error_sets_job_state_error(self, tmp_path):
        from pyimgtag.progress_db import ProgressDB

        _reset_job_for_tests()
        with ProgressDB(db_path=tmp_path / "test.db") as db:
            job = _Job(job_id="test-id", state="running")

            with patch.object(db, "get_images", side_effect=RuntimeError("db exploded")):
                with pytest.raises(RuntimeError, match="failed to load delete targets"):
                    _run_job(db, job)

            assert job.state == "error"
            assert job.last_error == "db_error"


class TestRunDriftPruneJob:
    """_run_drift_prune_job error paths (lines 222-252)."""

    def test_scan_failure_sets_scan_failed(self, tmp_path):
        from pyimgtag.progress_db import ProgressDB

        _reset_job_for_tests()
        with ProgressDB(db_path=tmp_path / "test.db") as db:
            job = _Job(job_id="drift-id", state="running")

            with patch(
                "pyimgtag.cleanup_drift.scan_drift",
                side_effect=RuntimeError("scan blew up"),
            ):
                with pytest.raises(RuntimeError, match="drift scan failed"):
                    _run_drift_prune_job(db, job)

            assert job.state == "error"
            assert job.last_error == "scan_failed"

    def test_prune_failure_sets_prune_failed(self, tmp_path):
        from pyimgtag.cleanup_drift import DriftReport
        from pyimgtag.progress_db import ProgressDB

        _reset_job_for_tests()
        with ProgressDB(db_path=tmp_path / "test.db") as db:
            job = _Job(job_id="drift-id2", state="running")

            fake_report = DriftReport(
                total=2,
                disk_missing=2,
                photos_missing=0,
                dead_paths=["/fake/a.jpg", "/fake/b.jpg"],
            )

            with (
                patch(
                    "pyimgtag.cleanup_drift.scan_drift",
                    return_value=fake_report,
                ),
                patch(
                    "pyimgtag.cleanup_drift.prune_drift",
                    side_effect=RuntimeError("prune blew up"),
                ),
            ):
                with pytest.raises(RuntimeError, match="drift prune failed"):
                    _run_drift_prune_job(db, job)

            assert job.state == "error"
            assert job.last_error == "prune_failed"

    def test_no_dead_paths_marks_done_early(self, tmp_path):
        """Empty dead_paths short-circuits to ``done`` without pruning (lines 238-242)."""
        from pyimgtag.cleanup_drift import DriftReport

        _reset_job_for_tests()
        with ProgressDB(db_path=tmp_path / "test.db") as db:
            job = _Job(job_id="drift-empty", state="running")
            report = DriftReport(total=5, disk_missing=0, photos_missing=0, dead_paths=[])
            with patch("pyimgtag.cleanup_drift.scan_drift", return_value=report):
                _run_drift_prune_job(db, job)
            assert job.state == "done"
            assert job.total == 0
            assert job.finished_at is not None

    def test_success_path_records_probe_error_and_recent(self, tmp_path):
        """Probe error surfaces as last_error; prune deletes and records (lines 236, 254-262)."""
        from pyimgtag.cleanup_drift import DriftReport

        _reset_job_for_tests()
        with ProgressDB(db_path=tmp_path / "test.db") as db:
            job = _Job(job_id="drift-ok", state="running")
            dead = [f"/gone/{i}.jpg" for i in range(3)]
            report = DriftReport(
                total=10,
                disk_missing=3,
                photos_missing=0,
                dead_paths=dead,
                photos_probe_error="photos_timeout",
            )
            with (
                patch("pyimgtag.cleanup_drift.scan_drift", return_value=report),
                patch("pyimgtag.cleanup_drift.prune_drift", return_value=3) as prune,
            ):
                _run_drift_prune_job(db, job)
            prune.assert_called_once_with(db, dead)
            assert job.state == "done"
            assert job.ok == 3
            assert job.done == report.dead_count
            assert job.last_error == "photos_timeout"
            assert len(job.recent) == 3


class TestCategoriseApplescriptError:
    """Cover every branch of _categorise_applescript_error (lines 96-122)."""

    @pytest.mark.parametrize(
        ("err", "expected"),
        [
            ("This requires macOS to run", "platform_unsupported"),
            ("operation timed out waiting for Photos", "photos_timeout"),
            ("AppleScript error (-1719) System Events", "accessibility_denied"),
            ("error (-25204) assistive access denied", "accessibility_denied"),
            ("needs accessibility permission", "accessibility_denied"),
            ('error "Photo not found: a.jpg" number -2700', "photo_not_in_library"),
            ("osascript failed to talk to Photos", "photos_unavailable"),
            ("some applescript glitch", "photos_unavailable"),
            ("totally unexpected gremlin", "photos_error"),
        ],
    )
    def test_categories(self, err, expected):
        assert _categorise_applescript_error(err) == expected


class TestSnapshot:
    def test_snapshot_shape(self):
        job = _Job(job_id="x", state="running", total=4, done=2, ok=1, failed=1)
        job.recent.append({"file_name": "a.jpg", "status": "ok"})
        snap = _snapshot(job)
        assert snap["job_id"] == "x"
        assert snap["state"] == "running"
        assert snap["total"] == 4
        assert snap["recent"] == [{"file_name": "a.jpg", "status": "ok"}]


class TestRunJobSuccessAndPerRowErrors:
    """_run_job loop body: success, db-row delete failure, applescript failure."""

    def _seed(self, db, tmp_path, names):
        for n in names:
            img = tmp_path / n
            img.write_bytes(b"\x00")
            db.mark_done(
                img,
                ImageResult(
                    file_path=str(img),
                    file_name=n,
                    tags=[],
                    cleanup_class="delete",
                ),
            )

    def test_success_deletes_rows(self, tmp_path):
        _reset_job_for_tests()
        with ProgressDB(db_path=tmp_path / "test.db") as db:
            self._seed(db, tmp_path, ["a.jpg", "b.jpg"])
            job = _Job(job_id="run-ok", state="running")
            with patch.object(routes_edit, "_run_job", wraps=routes_edit._run_job):
                with patch("pyimgtag.applescript_writer.delete_from_photos", return_value=None):
                    _run_job(db, job)
            assert job.state == "done"
            assert job.ok == 2
            assert job.failed == 0
            assert job.done == 2
            # Rows removed from the DB after a confirmed Photos delete.
            assert db.count_images(cleanup_class="delete") == 0

    def test_db_delete_failure_records_db_error(self, tmp_path):
        _reset_job_for_tests()
        with ProgressDB(db_path=tmp_path / "test.db") as db:
            self._seed(db, tmp_path, ["a.jpg"])
            job = _Job(job_id="run-dbfail", state="running")
            with (
                patch("pyimgtag.applescript_writer.delete_from_photos", return_value=None),
                patch.object(db, "delete_image", side_effect=RuntimeError("locked")),
            ):
                _run_job(db, job)
            assert job.state == "done"
            assert job.failed == 1
            assert job.done == 1
            assert job.recent[-1]["error"] == "db_error"

    def test_applescript_failure_records_category(self, tmp_path):
        _reset_job_for_tests()
        with ProgressDB(db_path=tmp_path / "test.db") as db:
            self._seed(db, tmp_path, ["a.jpg"])
            job = _Job(job_id="run-asfail", state="running")
            with patch(
                "pyimgtag.applescript_writer.delete_from_photos",
                return_value="osascript could not reach Photos",
            ):
                _run_job(db, job)
            assert job.state == "done"
            assert job.failed == 1
            assert job.last_error == "photos_unavailable"
            # The failed AppleScript leaves the DB row in place for retry.
            assert db.count_images(cleanup_class="delete") == 1


def _edit_client(tmp_path, seed_delete=0):
    db = ProgressDB(db_path=tmp_path / "progress.db")
    for i in range(seed_delete):
        img = tmp_path / f"d{i}.jpg"
        img.write_bytes(b"\x00")
        db.mark_done(
            img,
            ImageResult(
                file_path=str(img),
                file_name=f"d{i}.jpg",
                tags=[],
                cleanup_class="delete",
            ),
        )
    app = FastAPI()
    app.include_router(build_edit_router(db, api_base="/edit"), prefix="/edit")
    return db, TestClient(app)


class TestEditRouterEndpoints:
    def test_index_html_render(self, tmp_path):
        _reset_job_for_tests()
        _, client = _edit_client(tmp_path)
        r = client.get("/edit/")
        assert r.status_code == 200
        assert "/edit/api/marked" in r.text

    def test_render_edit_html_helper(self):
        html = render_edit_html("/edit")
        assert "/edit/api/marked" in html
        assert 'href="/edit"' in html

    def test_marked_endpoint(self, tmp_path):
        _reset_job_for_tests()
        _, client = _edit_client(tmp_path, seed_delete=3)
        r = client.get("/edit/api/marked")
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 3
        assert len(body["sample"]) == 3

    def test_status_endpoint(self, tmp_path):
        _reset_job_for_tests()
        _, client = _edit_client(tmp_path)
        r = client.get("/edit/api/status")
        assert r.status_code == 200
        assert r.json()["state"] == "idle"

    def test_run_requires_confirmation(self, tmp_path):
        _reset_job_for_tests()
        _, client = _edit_client(tmp_path)
        r = client.post("/edit/api/run", json={"confirm": False})
        assert r.status_code == 400
        assert r.json()["error"] == "confirmation_required"

    def test_run_spawns_job(self, tmp_path):
        _reset_job_for_tests()
        _, client = _edit_client(tmp_path, seed_delete=1)
        # Patch Thread so no real worker runs; the job state stays "running".
        with patch.object(routes_edit.threading, "Thread") as Thread:
            r = client.post("/edit/api/run", json={"confirm": True})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert "job_id" in body
        Thread.assert_called_once()
        _reset_job_for_tests()

    def test_run_rejects_overlapping_job(self, tmp_path):
        _reset_job_for_tests()
        _, client = _edit_client(tmp_path, seed_delete=1)
        # First call leaves _JOB in state "running" (Thread mocked out).
        with patch.object(routes_edit.threading, "Thread"):
            first = client.post("/edit/api/run", json={"confirm": True})
            assert first.status_code == 200
            second = client.post("/edit/api/run", json={"confirm": True})
        assert second.status_code == 400
        assert second.json()["error"] == "job_already_running"
        _reset_job_for_tests()

    def test_run_runner_swallows_worker_exception(self, tmp_path):
        """Exercise the _runner closure body, including the handled-exception path."""
        _reset_job_for_tests()
        _, client = _edit_client(tmp_path, seed_delete=1)
        captured = {}

        def _fake_thread(target=None, name=None, daemon=None):
            captured["target"] = target
            return MagicMock()

        with (
            patch.object(routes_edit.threading, "Thread", side_effect=_fake_thread),
            patch.object(routes_edit, "_run_job", side_effect=RuntimeError("worker boom")),
        ):
            r = client.post("/edit/api/run", json={"confirm": True})
            assert r.status_code == 200
            # Run the captured runner: it must swallow the worker exception.
            captured["target"]()
        _reset_job_for_tests()

    def test_drift_endpoint(self, tmp_path):
        _reset_job_for_tests()
        _, client = _edit_client(tmp_path)
        from pyimgtag.cleanup_drift import DriftReport

        report = DriftReport(
            total=4,
            disk_missing=1,
            photos_missing=2,
            dead_paths=["/gone/x.jpg"],
            photos_probe_error="photos_timeout",
        )
        with patch("pyimgtag.cleanup_drift.scan_drift", return_value=report):
            r = client.get("/edit/api/drift")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 4
        assert body["disk_missing"] == 1
        assert body["photos_missing"] == 2
        assert body["photos_probe_error"] == "photos_timeout"
        assert body["sample"] == ["/gone/x.jpg"]

    def test_prune_drift_requires_confirmation(self, tmp_path):
        _reset_job_for_tests()
        _, client = _edit_client(tmp_path)
        r = client.post("/edit/api/prune-drift", json={"confirm": False})
        assert r.status_code == 400
        assert r.json()["error"] == "confirmation_required"

    def test_prune_drift_spawns_job(self, tmp_path):
        _reset_job_for_tests()
        _, client = _edit_client(tmp_path)
        with patch.object(routes_edit.threading, "Thread") as Thread:
            r = client.post("/edit/api/prune-drift", json={"confirm": True})
        assert r.status_code == 200
        assert r.json()["ok"] is True
        Thread.assert_called_once()
        _reset_job_for_tests()

    def test_prune_drift_rejects_overlapping_job(self, tmp_path):
        _reset_job_for_tests()
        _, client = _edit_client(tmp_path)
        with patch.object(routes_edit.threading, "Thread"):
            first = client.post("/edit/api/prune-drift", json={"confirm": True})
            assert first.status_code == 200
            second = client.post("/edit/api/prune-drift", json={"confirm": True})
        assert second.status_code == 400
        assert second.json()["error"] == "job_already_running"
        _reset_job_for_tests()

    def test_prune_drift_runner_swallows_worker_exception(self, tmp_path):
        _reset_job_for_tests()
        _, client = _edit_client(tmp_path)
        captured = {}

        def _fake_thread(target=None, name=None, daemon=None):
            captured["target"] = target
            return MagicMock()

        with (
            patch.object(routes_edit.threading, "Thread", side_effect=_fake_thread),
            patch.object(
                routes_edit,
                "_run_drift_prune_job",
                side_effect=RuntimeError("worker boom"),
            ),
        ):
            r = client.post("/edit/api/prune-drift", json={"confirm": True})
            assert r.status_code == 200
            captured["target"]()
        _reset_job_for_tests()


class TestEditRouterImportGuard:
    def test_missing_fastapi_raises_importerror(self, tmp_path):
        db = ProgressDB(db_path=tmp_path / "guard.db")
        with patch.dict("sys.modules", {"fastapi": None}):
            with pytest.raises(ImportError, match="fastapi and uvicorn are required"):
                build_edit_router(db)
