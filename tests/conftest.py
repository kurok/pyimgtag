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
