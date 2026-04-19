"""Smoke tests for examples/mock_ollama.py so the demo mock stays compatible
with pyimgtag's preflight health checks."""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
MOCK_PATH = REPO_ROOT / "examples" / "mock_ollama.py"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def mock_server():
    port = _free_port()
    proc = subprocess.Popen(  # noqa: S603
        [sys.executable, str(MOCK_PATH), str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            requests.get(f"{base}/api/tags", timeout=0.5)
            break
        except requests.RequestException:
            time.sleep(0.05)
    else:
        proc.kill()
        proc.wait(timeout=2)
        raise RuntimeError("mock_ollama did not start in time")
    try:
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)


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
