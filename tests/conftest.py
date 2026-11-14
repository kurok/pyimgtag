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

    monkeypatch.setattr("pyimgtag.geocoder.ReverseGeocoder._last_request_ts", 0.0)
    monkeypatch.setattr(concurrent_pipeline, "_global_limiter", None)


@pytest.fixture(autouse=True)
def _ollama_model_is_pulled(monkeypatch):
    """Assume the configured model is pulled unless a test says otherwise.

    ``run`` and ``judge`` check the server and then the model before any work.
    Tests that fake a reachable server by patching ``check_ollama`` would
    otherwise reach the real ``/api/tags`` for the model check. Tests of the
    model check patch ``pyimgtag.commands.<run|judge>.check_ollama_model``
    themselves; ``pyimgtag.preflight.check_ollama_model`` is never patched.
    """
    from pyimgtag.commands import judge, run

    for module in (run, judge):
        monkeypatch.setattr(module, "check_ollama_model", lambda *_a, **_k: (True, "present"))
