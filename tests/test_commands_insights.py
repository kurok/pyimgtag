"""Tests for ``pyimgtag insights`` (CLI wiring + terminal/JSON/HTML renderers)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from pyimgtag import insights_report
from pyimgtag.insights_report import (
    TERMINAL_WIDTH,
    format_bytes,
    render_html,
    render_terminal,
)
from pyimgtag.main import build_parser, main
from pyimgtag.models import ImageResult, JudgeResult, JudgeScores
from pyimgtag.progress_db import ProgressDB

_EXTERNAL_URL = re.compile(r"""(?:src|href)\s*=\s*['"]?\s*(?:https?:)?//""", re.IGNORECASE)


def _seed(db_path: Path, *, with_judge: bool = True) -> None:
    with ProgressDB(db_path=db_path) as db:
        for i, tag in enumerate(("beach", "beach", "city")):
            path = Path(f"/lib/{i}.jpg")
            db.mark_done(
                path,
                ImageResult(
                    file_path=str(path),
                    file_name=path.name,
                    source_type="directory",
                    tags=[tag],
                    scene_summary="s",
                    image_date=f"2024-0{i + 1}-01",
                    nearest_country="Portugal",
                    processing_status="ok",
                ),
            )
            if with_judge:
                db.save_judge_result(
                    JudgeResult(
                        file_path=str(path),
                        file_name=path.name,
                        weighted_score=5 + i,
                        core_score=5 + i,
                        visible_score=5 + i,
                        scores=JudgeScores(score=5 + i, reason="<b>bold & brave</b>"),
                    )
                )


# --- parser ------------------------------------------------------------------


def test_parser_registers_insights_subcommand():
    args = build_parser().parse_args(["insights"])
    assert args.subcommand == "insights"
    assert args.format == "terminal"
    assert args.output is None
    assert args.top == 10
    assert args.no_thumbnails is False


def test_parser_accepts_all_flags(tmp_path):
    args = build_parser().parse_args(
        [
            "insights",
            "--db",
            str(tmp_path / "x.db"),
            "--format",
            "json",
            "-o",
            "out.json",
            "--top",
            "25",
            "--max-thumbnails",
            "3",
            "--no-thumbnails",
        ]
    )
    assert args.format == "json" and args.output == "out.json"
    assert args.top == 25 and args.max_thumbnails == 3 and args.no_thumbnails


def test_parser_rejects_unknown_format():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["insights", "--format", "xml"])


# --- main dispatch ------------------------------------------------------------


def test_main_terminal_output(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("PYIMGTAG_NO_UPDATE_CHECK", "1")
    db = tmp_path / "p.db"
    _seed(db)
    assert main(["insights", "--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert "pyimgtag library insights" in out
    assert "Photos in DB" in out and "3" in out
    assert "beach" in out and "Portugal" in out
    assert all(len(line) <= TERMINAL_WIDTH for line in out.splitlines())


def test_main_json_output_is_stable_document(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("PYIMGTAG_NO_UPDATE_CHECK", "1")
    db = tmp_path / "p.db"
    _seed(db)
    assert main(["insights", "--db", str(db), "--format", "json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["schema_version"] == 1
    assert doc["overview"]["total"] == 3
    assert doc["content"]["top_tags"][0] == {"value": "beach", "count": 2}
    # Golden top-level shape — bump schema_version if this changes.
    assert set(doc) == {
        "schema_version",
        "empty",
        "overview",
        "time",
        "places",
        "content",
        "quality",
        "housekeeping",
    }


def test_main_output_html_file_is_self_contained(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("PYIMGTAG_NO_UPDATE_CHECK", "1")
    db = tmp_path / "p.db"
    _seed(db)
    out = tmp_path / "report.html"
    # Format is inferred from the .html extension.
    assert main(["insights", "--db", str(db), "--output", str(out), "--no-thumbnails"]) == 0
    assert "Wrote html report" in capsys.readouterr().err
    html = out.read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE html>")
    assert "<title>pyimgtag library insights</title>" in html
    assert not _EXTERNAL_URL.search(html), "HTML report must not reference external URLs"
    assert "http://" not in html and "https://" not in html
    assert "<link" not in html and "<script" not in html
    # Untrusted DB text is escaped.
    assert "<b>bold" not in html and "&lt;b&gt;bold &amp; brave" in html


def test_main_output_json_extension_infers_format(tmp_path, monkeypatch):
    monkeypatch.setenv("PYIMGTAG_NO_UPDATE_CHECK", "1")
    db = tmp_path / "p.db"
    _seed(db, with_judge=False)
    out = tmp_path / "report.json"
    assert main(["insights", "--db", str(db), "-o", str(out)]) == 0
    doc = json.loads(out.read_text())
    assert "quality" not in doc


def test_main_empty_db_is_friendly(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("PYIMGTAG_NO_UPDATE_CHECK", "1")
    db = tmp_path / "empty.db"
    assert main(["insights", "--db", str(db)]) == 0
    captured = capsys.readouterr()
    assert "Nothing tagged yet" in captured.out
    assert "nothing tagged yet" in captured.err.lower()


def test_main_html_with_thumbnails_reads_only_db_known_files(tmp_path, monkeypatch):
    """Thumbnail loader only ever receives paths from the DB, capped by --max-thumbnails."""
    monkeypatch.setenv("PYIMGTAG_NO_UPDATE_CHECK", "1")
    db = tmp_path / "p.db"
    _seed(db)
    seen: list[str] = []

    def fake_loader(path: str, size: int) -> bytes | None:
        seen.append(path)
        return b"\xff\xd8fakejpeg"

    monkeypatch.setattr("pyimgtag.commands.insights._thumb_loader", fake_loader)
    out = tmp_path / "r.html"
    assert main(["insights", "--db", str(db), "-o", str(out), "--max-thumbnails", "2"]) == 0
    assert seen == ["/lib/2.jpg", "/lib/1.jpg"]  # top-2 by score, DB paths only
    html = out.read_text()
    assert html.count("data:image/jpeg;base64,") == 2
    assert "0.jpg" in html  # third photo still listed, with a placeholder


# --- renderer unit tests --------------------------------------------------------


def test_format_bytes():
    assert format_bytes(None) == "0 B"
    assert format_bytes(512) == "512 B"
    assert format_bytes(2048) == "2.0 KB"
    assert format_bytes(3 * 1024**3) == "3.0 GB"
    assert format_bytes(5 * 1024**4) == "5.0 TB"


def test_render_terminal_empty():
    text = render_terminal({"schema_version": 1, "empty": True, "overview": {"total": 0}})
    assert "Nothing tagged yet" in text


def test_render_html_empty_has_no_external_refs():
    html = render_html({"schema_version": 1, "empty": True, "overview": {"total": 0}})
    assert "Nothing tagged yet" in html
    assert not _EXTERNAL_URL.search(html)


def test_render_html_thumb_loader_failure_is_swallowed(tmp_path):
    _seed(tmp_path / "p.db")
    with ProgressDB(db_path=tmp_path / "p.db") as db:
        doc = db.get_insights()

    def boom(path: str, size: int) -> bytes | None:
        raise RuntimeError("decode failed")

    html = render_html(doc, thumb_loader=boom)
    assert "data:image" not in html
    assert "2.jpg" in html


def test_render_html_generated_at_override(tmp_path):
    _seed(tmp_path / "p.db")
    with ProgressDB(db_path=tmp_path / "p.db") as db:
        doc = db.get_insights()
    html = render_html(doc, generated_at="2026-01-01 00:00 UTC")
    assert "Generated 2026-01-01 00:00 UTC" in html


def test_thumb_loader_uses_review_pipeline(tmp_path, monkeypatch):
    from pyimgtag.commands import insights as cmd

    monkeypatch.setattr(
        "pyimgtag.webapp.routes_review._make_thumbnail", lambda p, s: f"{p}:{s}".encode()
    )
    assert cmd._thumb_loader("/a.jpg", 320) == b"/a.jpg:320"


def test_terminal_width_constant_matches_ac():
    assert insights_report.TERMINAL_WIDTH == 100
