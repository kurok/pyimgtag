"""End to end: ``run`` and ``judge`` exit 3 when every model call failed.

Runs the real ``python -m pyimgtag`` in a child process (see
``tests/e2e_support.py``) against a tiny fake Ollama in this process: it lists
the model under ``/api/tags`` and answers ``/api/chat`` with either an HTTP
500 or a valid reply. Nothing leaves 127.0.0.1.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image

from tests.e2e_support import run_cli

_MODEL = "gemma4:e4b"
_TAG_REPLY = json.dumps(
    {
        "tags": ["beach"],
        "summary": "A beach.",
        "scene_category": "outdoor_leisure",
        "emotional_tone": "positive",
        "cleanup_class": "keep",
        "has_text": False,
        "text_summary": None,
        "event_hint": "outing",
        "significance": "low",
    }
)
_JUDGE_REPLY = json.dumps({"score": 7, "reason": "fine"})


@contextmanager
def _fake_ollama(chat_ok: bool) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload: object) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 — http.server API
            self._send(200, {"models": [{"name": _MODEL}]})

        def do_POST(self) -> None:  # noqa: N802 — http.server API
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if not chat_ok:
                self._send(500, {"error": "model crashed"})
                return
            prompt = request["messages"][-1]["content"]
            reply = _JUDGE_REPLY if '"score"' in prompt else _TAG_REPLY
            self._send(200, {"message": {"content": reply}})

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
    for name in ("a.jpg", "b.jpg"):
        Image.new("RGB", (16, 16), (90, 140, 200)).save(photos / name)
    return photos


def _run(tmp_path: Path, url: str) -> object:
    return run_cli(
        "run",
        "--input-dir",
        str(_photos(tmp_path)),
        "--db",
        str(tmp_path / "x.db"),
        "--ollama-url",
        url,
        "--model",
        _MODEL,
        "--no-web",
        "--timeout",
        "10",
        home=tmp_path,
    )


def test_run_exits_3_when_every_model_call_failed(tmp_path: Path) -> None:
    with _fake_ollama(chat_ok=False) as url:
        result = _run(tmp_path, url)
    assert result.returncode == 3, result.stderr
    assert "Model failures:   2" in result.stderr


def test_run_exits_0_when_the_model_answers(tmp_path: Path) -> None:
    with _fake_ollama(chat_ok=True) as url:
        result = _run(tmp_path, url)
    assert result.returncode == 0, result.stderr


def test_judge_exits_3_and_says_why(tmp_path: Path) -> None:
    with _fake_ollama(chat_ok=False) as url:
        result = run_cli(
            "judge",
            "--input-dir",
            str(_photos(tmp_path)),
            "--db",
            str(tmp_path / "x.db"),
            "--ollama-url",
            url,
            "--model",
            _MODEL,
            "--no-web",
            home=tmp_path,
        )
    assert result.returncode == 3, result.stderr
    assert "judge failed (request failed: HTTP 500), skipping" in result.stderr
