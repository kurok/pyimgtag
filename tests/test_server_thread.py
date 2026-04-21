"""Tests for the DashboardServer background-thread wrapper."""

from __future__ import annotations

import socket
import time
import urllib.request

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")

from pyimgtag.webapp.dashboard_server import create_app  # noqa: E402
from pyimgtag.webapp.server_thread import DashboardServer  # noqa: E402


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_start_serves_and_stop_shuts_down():
    port = _free_port()
    server = DashboardServer(create_app(), host="127.0.0.1", port=port)
    server.start()
    try:
        # Poll up to 3s for readiness.
        deadline = time.monotonic() + 3.0
        body = None
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/run/current") as r:
                    body = r.read()
                    break
            except OSError:
                time.sleep(0.05)
        assert body is not None, "server never became ready"
    finally:
        server.stop()

    # After stop, requests should fail.
    with pytest.raises(OSError):
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/run/current", timeout=1.0)


def test_url_property():
    server = DashboardServer(create_app(), host="127.0.0.1", port=65432)
    assert server.url == "http://127.0.0.1:65432"
