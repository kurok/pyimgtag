"""Tests for routes_edit error-path branches."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")

from unittest.mock import patch  # noqa: E402

from pyimgtag.webapp.routes_edit import (  # noqa: E402
    _Job,
    _reset_job_for_tests,
    _run_drift_prune_job,
    _run_job,
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
