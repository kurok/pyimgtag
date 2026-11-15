"""End to end: the vocabulary summary says what it leaves out (#439).

A real ``pyimgtag run --vocabulary`` against a fake Ollama whose reply has 20
off-vocabulary tags: the summary lists 15 and ends with "… and 5 more tags",
pointing at the ``.vocabulary.json`` report that holds the full list.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from tests.e2e_support import fake_ollama, run_cli

_OFF_VOCAB = [f"thing{i:02d}" for i in range(20)]


def _reply(tags: list[str]) -> str:
    return json.dumps(
        {
            "tags": tags,
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


# Five photos, four different off-vocabulary tags each (five tags per photo).
_REPLIES = [_reply(["beach", *_OFF_VOCAB[i : i + 4]]) for i in range(0, 20, 4)]


def test_the_summary_marks_the_hidden_tags_and_points_at_the_report(tmp_path: Path) -> None:
    photos = tmp_path / "photos"
    photos.mkdir()
    for i in range(5):
        Image.new("RGB", (32, 32), (10 + 40 * i, 120, 200)).save(photos / f"p{i}.jpg")
    vocab = tmp_path / "vocab.json"
    vocab.write_text(json.dumps({"tags": ["beach"]}), encoding="utf-8")
    out = tmp_path / "out.json"
    with fake_ollama(tag_replies=_REPLIES) as url:
        result = run_cli(
            "run", "--input-dir", str(photos), "--ollama-url", url, "--model", "gemma4",
            "--vocabulary", str(vocab), "--output-json", str(out), "--no-web",
            "--db", str(tmp_path / "p.db"), home=tmp_path,
        )  # fmt: skip
    assert result.returncode == 0, result.stderr
    listed = [line for line in result.stderr.splitlines() if "(kept x" in line]
    assert len(listed) == 15
    report = tmp_path / "out.vocabulary.json"
    assert f"… and 5 more distinct tags (full list in {report})" in result.stderr
    kept = json.loads(report.read_text())["mapping"]["kept_off_vocabulary"]
    assert len(kept) == 20
