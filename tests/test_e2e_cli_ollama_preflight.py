"""End to end: ``run`` and ``preflight`` stop with a fix when Ollama is not usable.

Runs the real ``python -m pyimgtag`` in a child process (see
``tests/e2e_support.py``) against either a closed loopback port or a tiny
fake Ollama server in this process that answers ``/api/tags``. Nothing leaves
127.0.0.1.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tests.e2e_support import free_port, run_cli


@contextmanager
def _fake_ollama(*models: str) -> Iterator[str]:
    """Serve ``GET /api/tags`` listing *models*; yield the base URL."""
    body = json.dumps({"models": [{"name": m} for m in models]}).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — http.server API
            self.send_response(200 if self.path == "/api/tags" else 404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def _photos(tmp_path: Path) -> Path:
    photos = tmp_path / "photos"
    photos.mkdir()
    for name in ("p0.jpg", "p1.jpg", "p2.jpg"):
        (photos / name).write_bytes(b"\xff\xd8\xff\xd9")
    return photos


def test_run_stops_before_any_work_when_ollama_is_unreachable(tmp_path: Path) -> None:
    url = f"http://127.0.0.1:{free_port()}"  # nothing listens there
    db = tmp_path / "x.db"

    result = run_cli(
        "run",
        "--input-dir",
        str(_photos(tmp_path)),
        "--db",
        str(db),
        "--ollama-url",
        url,
        "--no-web",
        "--timeout",
        "3",
        home=tmp_path,
    )

    assert result.returncode == 1
    assert f"Error: Ollama is not reachable at {url}." in result.stderr
    assert "ollama serve" in result.stderr
    assert "HTTPConnectionPool" not in result.stderr
    assert "[1/3]" not in result.stderr + result.stdout
    assert not db.exists()


def test_run_stops_when_the_model_is_not_pulled(tmp_path: Path) -> None:
    db = tmp_path / "x.db"
    with _fake_ollama("llama3:8b") as url:
        result = run_cli(
            "run",
            "--input-dir",
            str(_photos(tmp_path)),
            "--db",
            str(db),
            "--ollama-url",
            url,
            "--model",
            "gemma4:e4b",
            "--no-web",
            home=tmp_path,
        )

    assert result.returncode == 1
    assert "Error: model 'gemma4:e4b' is not pulled. Run 'ollama pull gemma4:e4b'." in (
        result.stderr
    )
    assert not db.exists()


def test_preflight_gives_the_same_remediation(tmp_path: Path) -> None:
    down = run_cli("preflight", "--ollama-url", f"http://127.0.0.1:{free_port()}", home=tmp_path)
    assert "ollama serve" in down.stdout + down.stderr
    assert "HTTPConnectionPool" not in down.stdout + down.stderr

    with _fake_ollama("llama3:8b") as url:
        missing = run_cli("preflight", "--ollama-url", url, "--model", "gemma4", home=tmp_path)
    assert "ollama pull gemma4" in missing.stdout + missing.stderr

    with _fake_ollama("gemma4:latest") as url:
        present = run_cli("preflight", "--ollama-url", url, "--model", "gemma4", home=tmp_path)
    assert "Model 'gemma4' is available" in present.stdout + present.stderr


def test_judge_stops_with_the_same_lines_as_run(tmp_path: Path) -> None:
    photos = _photos(tmp_path)
    down_url = f"http://127.0.0.1:{free_port()}"
    down = run_cli(
        "judge", "--input-dir", str(photos), "--ollama-url", down_url, "--no-web", home=tmp_path
    )
    assert down.returncode == 1
    assert f"Error: Ollama is not reachable at {down_url}." in down.stderr

    with _fake_ollama("llama3:8b") as url:
        missing = run_cli(
            "judge",
            "--input-dir",
            str(photos),
            "--ollama-url",
            url,
            "--model",
            "gemma4",
            "--no-web",
            home=tmp_path,
        )
    assert missing.returncode == 1
    assert "Error: model 'gemma4' is not pulled. Run 'ollama pull gemma4'." in missing.stderr
