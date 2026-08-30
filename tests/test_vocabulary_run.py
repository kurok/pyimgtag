"""End-to-end: ``run --vocabulary/--prompt-template/--tag-language`` and query roll-up."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pyimgtag.main import build_parser, main
from pyimgtag.models import TagResult
from pyimgtag.ollama_client import _PROMPT_FIELDS
from pyimgtag.progress_db import ProgressDB

VOCAB = {
    "tags": [
        {"beach": {"synonyms": ["seaside", "shore"]}},
        "hiking",
        {"food": {"children": ["restaurant", "baking"]}},
    ],
    "strict": False,
}


def _vocab_file(tmp_path: Path, strict: bool = False) -> Path:
    p = tmp_path / "vocab.json"
    p.write_text(json.dumps(dict(VOCAB, strict=strict)), encoding="utf-8")
    return p


def _photos(tmp_path: Path, n: int = 3) -> Path:
    d = tmp_path / "photos"
    d.mkdir()
    for i in range(n):
        (d / f"p{i}.jpg").write_bytes(b"\xff\xd8x")
    return d


def _run(argv: list[str], responses: list[list[str]], capture_prompts: list | None = None):
    """Run ``pyimgtag run`` with a stub client returning ``responses`` in order."""
    parser = build_parser()
    args = parser.parse_args(["run", *argv])
    queue = list(responses)

    def tag_image(path, context=None):
        return TagResult(tags=queue.pop(0), summary="s")

    from pyimgtag.commands.run import cmd_run

    with (
        patch("pyimgtag.commands.run.check_ollama", return_value=(True, "")),
        patch("pyimgtag.commands.run.OllamaClient") as cls,
    ):
        client = MagicMock()
        client.tag_image.side_effect = tag_image
        client.prompt_builder = None
        cls.return_value = client
        rc = cmd_run(args, parser)
        if capture_prompts is not None:
            capture_prompts.append(client.prompt_builder)
    return rc


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setenv("PYIMGTAG_NO_UPDATE_CHECK", "1")
    monkeypatch.setenv("PYIMGTAG_NO_WEB", "1")
    monkeypatch.delenv("PYIMGTAG_VOCABULARY", raising=False)
    monkeypatch.delenv("PYIMGTAG_PROMPT_TEMPLATE", raising=False)


def test_run_canonicalizes_tags_and_reports(tmp_path, capsys):
    photos = _photos(tmp_path)
    db = tmp_path / "p.db"
    out = tmp_path / "out.json"
    builders: list = []
    rc = _run(
        [
            "--input-dir",
            str(photos),
            "--db",
            str(db),
            "--vocabulary",
            str(_vocab_file(tmp_path)),
            "--output-json",
            str(out),
            "--no-web",
        ],
        [["Seaside", "sunset"], ["shores", "Hiking"], ["Restaurants", "beach"]],
        builders,
    )
    assert rc == 0
    err = capsys.readouterr().err
    # Layer 1: the client got a prompt builder embedding the vocabulary.
    builder = builders[0]
    assert builder is not None
    prompt = builder.render(None)
    assert "beach, hiking, food, restaurant, baking" in prompt and prompt.endswith(_PROMPT_FIELDS)
    # Layer 2: DB rows hold canonical tags.
    with ProgressDB(db_path=db) as pdb:
        rows = {Path(r["file_path"]).name: r["tags_list"] for r in pdb.query_images()}
    assert rows == {
        "p0.jpg": ["beach", "sunset"],
        "p1.jpg": ["beach", "hiking"],
        "p2.jpg": ["restaurant", "beach"],
    }
    # Summary shows counts (seaside, shores, restaurants mapped; Hiking/beach exact).
    assert "--- Vocabulary ---" in err
    assert "Mapped:           3" in err
    assert "Exact matches:    2" in err
    assert "Kept off-vocab:   1" in err
    assert "seaside -> beach  x1" in err
    assert "Vocabulary: 5 tags, 2 synonyms (non-strict)" in err
    # JSON report next to --output-json.
    report = json.loads((tmp_path / "out.vocabulary.json").read_text())
    assert report["vocabulary"]["strict"] is False
    mapped = {(m["raw"], m["canonical"]): m["count"] for m in report["mapping"]["mapped"]}
    assert mapped == {
        ("seaside", "beach"): 1,
        ("shores", "beach"): 1,
        ("restaurants", "restaurant"): 1,
    }
    assert report["mapping"]["kept_off_vocabulary"] == [{"raw": "sunset", "count": 1}]
    assert "Wrote vocabulary mapping report" in err
    # Results JSON itself is untouched (still a plain list).
    assert isinstance(json.loads(out.read_text()), list)


def test_run_strict_drops_and_counts(tmp_path, capsys):
    photos = _photos(tmp_path, 1)
    rc = _run(
        [
            "--input-dir",
            str(photos),
            "--no-cache",
            "--dry-run",
            "--vocabulary",
            str(_vocab_file(tmp_path, strict=True)),
            "--no-web",
        ],
        [["sunset", "Shore", "clouds"]],
    )
    assert rc == 0
    err = capsys.readouterr().err
    assert "Dropped (strict): 2" in err
    assert "sunset  (dropped x1)" in err
    assert "-> beach, " in err or "beach" in err  # brief line shows canonical tag


def test_run_invalid_vocabulary_is_startup_error(tmp_path, capsys):
    photos = _photos(tmp_path, 1)
    bad = tmp_path / "bad.json"
    bad.write_text('{"tags": [{"a": {"color": 1}}]}', encoding="utf-8")
    rc = _run(["--input-dir", str(photos), "--no-cache", "--vocabulary", str(bad), "--no-web"], [])
    assert rc == 1
    assert "bad.json: tags[0].a: unknown option(s): color" in capsys.readouterr().err


def test_run_invalid_template_is_startup_error(tmp_path, capsys):
    photos = _photos(tmp_path, 1)
    tpl = tmp_path / "t.txt"
    tpl.write_text("Hello {nope}", encoding="utf-8")
    rc = _run(
        ["--input-dir", str(photos), "--no-cache", "--prompt-template", str(tpl), "--no-web"], []
    )
    assert rc == 1
    assert "t.txt: unknown placeholder(s) {nope}" in capsys.readouterr().err


def test_run_template_and_language_and_env_precedence(tmp_path, monkeypatch):
    photos = _photos(tmp_path, 1)
    env_tpl = tmp_path / "env.txt"
    env_tpl.write_text("ENV TEMPLATE {fields}", encoding="utf-8")
    flag_tpl = tmp_path / "flag.txt"
    flag_tpl.write_text("FLAG TEMPLATE {language}{fields}", encoding="utf-8")
    monkeypatch.setenv("PYIMGTAG_PROMPT_TEMPLATE", str(env_tpl))
    monkeypatch.setenv("PYIMGTAG_VOCABULARY", str(_vocab_file(tmp_path)))

    builders: list = []
    rc = _run(
        [
            "--input-dir",
            str(photos),
            "--no-cache",
            "--dry-run",
            "--prompt-template",
            str(flag_tpl),
            "--tag-language",
            "ru",
            "--no-web",
        ],
        [["beach"]],
        builders,
    )
    assert rc == 0
    prompt = builders[0].render(None)
    assert prompt.startswith("FLAG TEMPLATE")  # flag wins over env
    assert "in ru." in prompt and "Tags must stay exactly" in prompt  # env vocab still applied

    # Env only: template from env, no language.
    builders.clear()
    rc = _run(
        ["--input-dir", str(photos), "--no-cache", "--dry-run", "--no-web"], [["beach"]], builders
    )
    assert rc == 0
    assert builders[0].render(None).startswith("ENV TEMPLATE")


def test_run_without_customisation_leaves_client_prompt_alone(tmp_path):
    photos = _photos(tmp_path, 1)
    builders: list = []
    rc = _run(
        ["--input-dir", str(photos), "--no-cache", "--dry-run", "--no-web"], [["x"]], builders
    )
    assert rc == 0
    assert builders[0] is None


# --- query --include-children ---------------------------------------------------------


def _seed_db(tmp_path: Path) -> Path:
    from pyimgtag.models import ImageResult

    db = tmp_path / "q.db"
    with ProgressDB(db_path=db) as pdb:
        for name, tags in (
            ("a.jpg", ["food"]),
            ("b.jpg", ["restaurant", "beach"]),
            ("c.jpg", ["baking"]),
            ("d.jpg", ["seafood"]),  # substring of nothing we want; exact match must exclude it
            ("e.jpg", ["hiking"]),
        ):
            pdb.mark_done(
                Path(f"/lib/{name}"),
                ImageResult(file_path=f"/lib/{name}", file_name=name, tags=tags),
            )
    return db


def test_query_include_children_rolls_up(tmp_path, capsys):
    db = _seed_db(tmp_path)
    rc = main(
        [
            "query",
            "--db",
            str(db),
            "--tag",
            "food",
            "--include-children",
            "--vocabulary",
            str(_vocab_file(tmp_path)),
            "--format",
            "paths",
        ]
    )
    assert rc == 0
    captured = capsys.readouterr()
    names = sorted(Path(p).name for p in captured.out.split())
    assert names == ["a.jpg", "b.jpg", "c.jpg"]
    assert "Matching tags: food, restaurant, baking" in captured.err


def test_query_without_include_children_is_substring(tmp_path, capsys):
    db = _seed_db(tmp_path)
    assert main(["query", "--db", str(db), "--tag", "food", "--format", "paths"]) == 0
    names = sorted(Path(p).name for p in capsys.readouterr().out.split())
    assert names == ["a.jpg", "d.jpg"]


def test_query_include_children_via_env(tmp_path, capsys, monkeypatch):
    db = _seed_db(tmp_path)
    monkeypatch.setenv("PYIMGTAG_VOCABULARY", str(_vocab_file(tmp_path)))
    rc = main(
        ["query", "--db", str(db), "--tag", "Beach", "--include-children", "--format", "paths"]
    )
    assert rc == 0
    assert [Path(p).name for p in capsys.readouterr().out.split()] == ["b.jpg"]


def test_query_include_children_requires_tag_and_vocabulary(tmp_path):
    with pytest.raises(SystemExit):
        main(["query", "--include-children", "--vocabulary", "x.json"])
    with pytest.raises(SystemExit):
        main(["query", "--tag", "food", "--include-children"])


def test_query_include_children_bad_vocabulary(tmp_path, capsys):
    db = _seed_db(tmp_path)
    rc = main(
        [
            "query",
            "--db",
            str(db),
            "--tag",
            "food",
            "--include-children",
            "--vocabulary",
            "nope.json",
        ]
    )
    assert rc == 1
    assert "nope.json" in capsys.readouterr().err


def test_query_images_tags_any_exact_and_empty(tmp_path):
    db = _seed_db(tmp_path)
    with ProgressDB(db_path=db) as pdb:
        assert [Path(r["file_path"]).name for r in pdb.query_images(tags_any=["BEACH"])] == [
            "b.jpg"
        ]
        assert pdb.query_images(tags_any=[]) == []
        # Combinable with the substring filter.
        both = pdb.query_images(tag="rest", tags_any=["restaurant", "food"])
        assert [Path(r["file_path"]).name for r in both] == ["b.jpg"]
