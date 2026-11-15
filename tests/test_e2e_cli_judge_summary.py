"""End to end: ``pyimgtag judge`` explains an empty or failed result (#437).

Runs the real CLI in a child process against a tiny fake Ollama in this
process (``/api/tags`` plus ``/api/chat``). Nothing leaves 127.0.0.1.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from tests.e2e_support import fake_ollama, run_cli


def _fake_ollama(chat_status: int, score: int = 4):
    reply = json.dumps({"score": score, "reason": "fine"})
    return fake_ollama(chat_status=chat_status, judge_reply=reply)


def _photos(tmp_path: Path, n: int = 3) -> Path:
    photos = tmp_path / "photos"
    photos.mkdir()
    for i in range(n):
        Image.new("RGB", (32, 32), (i * 40, 90, 160)).save(photos / f"p{i}.jpg")
    return photos


def _judge(url: str, photos: Path, home: Path, *extra: str):
    return run_cli(
        "judge", "--input-dir", str(photos), "--ollama-url", url, "--model", "gemma4",
        "--no-web", "--db", str(home / "p.db"), *extra, home=home,
    )  # fmt: skip


def test_nothing_above_min_score_still_prints_a_verdict(tmp_path: Path) -> None:
    photos = _photos(tmp_path)
    with _fake_ollama(200, score=4) as url:
        result = _judge(url, photos, tmp_path, "--min-score", "7")
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr.rstrip().splitlines()[-1] == "Judged 3 photo(s): 0 at or above 7."
    assert "Below min score:  3" in result.stderr


def test_every_call_failing_exits_3_with_the_failures_in_the_summary(tmp_path: Path) -> None:
    photos = _photos(tmp_path)
    with _fake_ollama(500) as url:
        result = _judge(url, photos, tmp_path)
    assert result.returncode == 3, result.stderr
    assert "Model failures:   3" in result.stderr
    assert result.stderr.rstrip().splitlines()[-1] == "Judged 0 photo(s); 3 model call(s) failed."
