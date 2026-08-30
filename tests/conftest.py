"""Shared pytest fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _parse_error_log_in_tmp(tmp_path, monkeypatch):
    """Keep the Ollama parse-error log out of the invoking directory.

    ``ollama_client._log_parse_error`` defaults to
    ``./pyimgtag-parse-errors.log`` in the CWD, so any test that exercises an
    unparseable model response would otherwise append a file to wherever
    pytest was launched from (typically the repo root).
    """
    monkeypatch.setenv("PYIMGTAG_PARSE_ERROR_LOG", str(tmp_path / "parse-errors.log"))


@pytest.fixture(autouse=True)
def _reset_shared_throttles(monkeypatch):
    """Clear the process-wide Nominatim schedule and ``--max-rps`` bucket.

    Both are module globals so the limits hold across geocoder instances and
    ``-j`` worker threads; without a reset, one test's request would make the
    next test's first lookup sleep for the full Nominatim interval.
    """
    from pyimgtag import concurrent_pipeline

    monkeypatch.setattr("pyimgtag.geocoder._LAST_REQUEST_TS", 0.0)
    monkeypatch.setattr(concurrent_pipeline, "_global_limiter", None)
