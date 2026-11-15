"""End to end: a failed model call reads as one actionable line (#433).

Runs the real ``pyimgtag run`` against a fake Ollama that answers 500, and
against a closed port. The default output carries no urllib3 repr and no
mid-word cut; the raw detail lands in the DB row.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from PIL import Image

from tests.e2e_support import fake_ollama, free_port, run_cli


def _photos(tmp_path: Path) -> Path:
    photos = tmp_path / "photos"
    photos.mkdir()
    Image.new("RGB", (32, 32), (10, 120, 200)).save(photos / "p0.jpg")
    return photos


def _run(url: str, photos: Path, home: Path):
    return run_cli(
        "run", "--input-dir", str(photos), "--ollama-url", url, "--model", "gemma4",
        "--no-web", "--db", str(home / "p.db"), home=home,
    )  # fmt: skip


def _stored_error(home: Path) -> str:
    with sqlite3.connect(home / "p.db") as conn:
        (message,) = conn.execute("SELECT error_message FROM processed_images").fetchone()
    return message


def test_http_500_is_one_readable_line(tmp_path: Path) -> None:
    photos = _photos(tmp_path)
    with fake_ollama(chat_status=500) as url:
        result = _run(url, photos, tmp_path)
    output = result.stdout + result.stderr
    assert "error: Ollama returned HTTP 500" in output
    assert "Server Error: Internal Server Err" not in output
    assert "for url:" not in output
    assert "for url:" in _stored_error(tmp_path)


def test_an_unreachable_server_names_the_url(tmp_path: Path) -> None:
    """Preflight stops a run when Ollama is down, so tag one image directly."""
    from pyimgtag.ollama_client import OllamaClient

    url = f"http://127.0.0.1:{free_port()}"
    photo = _photos(tmp_path) / "p0.jpg"
    client = OllamaClient(model="gemma4", base_url=url, timeout=5)
    result = client.tag_image(str(photo))
    client.close()
    first, _, detail = (result.error or "").partition("\n")
    assert first == f"Ollama is not reachable at {url} — start it with 'ollama serve'"
    assert "ConnectionError" in detail
