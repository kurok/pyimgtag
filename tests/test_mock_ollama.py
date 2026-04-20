"""Smoke tests for examples/mock_ollama.py so the demo mock stays compatible
with pyimgtag's preflight health checks.

The handler class is loaded directly from the script (via importlib) and run
in a background thread to avoid flaky subprocess-startup timing on CI runners.
"""

from __future__ import annotations

import http.server
import importlib.util
import socket
import threading
from pathlib import Path

import pytest
import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
MOCK_PATH = REPO_ROOT / "examples" / "mock_ollama.py"


def _load_mock_module():
    spec = importlib.util.spec_from_file_location("mock_ollama_under_test", MOCK_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def mock_server():
    mod = _load_mock_module()
    port = _free_port()
    server = http.server.HTTPServer(("127.0.0.1", port), mod.MockOllamaHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


class TestMockOllamaTagsEndpoint:
    def test_api_tags_returns_default_model(self, mock_server):
        resp = requests.get(f"{mock_server}/api/tags", timeout=2)
        assert resp.status_code == 200
        payload = resp.json()
        assert "models" in payload
        names = [m.get("name") for m in payload["models"]]
        assert "gemma4:e4b" in names

    def test_preflight_check_ollama_succeeds_against_mock(self, mock_server):
        from pyimgtag.preflight import check_ollama, check_ollama_model

        ok, _ = check_ollama(mock_server)
        assert ok is True

        ok, _ = check_ollama_model("gemma4:e4b", mock_server)
        assert ok is True

    def test_unknown_get_path_returns_404(self, mock_server):
        resp = requests.get(f"{mock_server}/api/nope", timeout=2)
        assert resp.status_code == 404
