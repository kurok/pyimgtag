"""Tests for :mod:`pyimgtag.prompt_template` and the ``prompt`` subcommand."""

from __future__ import annotations

import json

import pytest

from pyimgtag.main import main
from pyimgtag.ollama_client import _PROMPT_BASE, _PROMPT_FIELDS, _build_prompt_with_context
from pyimgtag.prompt_template import (
    DEFAULT_TEMPLATE,
    MAX_TAGS,
    PromptBuilder,
    PromptTemplateError,
    load_prompt_template,
    validate_template,
)
from pyimgtag.vocabulary import Vocabulary


def test_default_builder_matches_builtin_prompt_without_context():
    assert PromptBuilder().render(None) == _PROMPT_BASE
    assert PromptBuilder().render({}) == _PROMPT_BASE
    assert PromptBuilder().is_default


def test_default_builder_matches_builtin_prompt_with_context():
    ctx = {"date": "2026-01-15", "city": "Paris", "country": "France", "lat": 1.5, "lon": 2.5}
    assert PromptBuilder().render(ctx) == _build_prompt_with_context(ctx)


def test_vocabulary_block_is_embedded():
    v = Vocabulary(tags=["beach", "hiking"], strict=True)
    prompt = PromptBuilder(vocabulary=v).render(None)
    assert "Allowed tags" in prompt and "beach, hiking" in prompt
    assert prompt.endswith(_PROMPT_FIELDS)
    assert not PromptBuilder(vocabulary=v).is_default


def test_language_block_plain_and_with_vocabulary():
    plain = PromptBuilder(language="ru").render(None)
    assert "Write the tags, summary, and text_summary in ru." in plain
    v = Vocabulary(tags=["beach"])
    with_vocab = PromptBuilder(vocabulary=v, language="Portuguese").render(None)
    assert "Write the summary and text_summary in Portuguese." in with_vocab
    assert "Tags must stay exactly as written" in with_vocab
    assert PromptBuilder(language="  ").is_default


def test_custom_template_placeholders_render():
    tpl = "Field guide.\n{context}{vocabulary}{language}Max {max_tags} tags.\n{fields}"
    prompt = PromptBuilder(template=tpl, language="de").render({"city": "Bonn"})
    assert prompt.startswith("Field guide.\nContext")
    assert "- Location: Bonn" in prompt
    assert f"Max {MAX_TAGS} tags." in prompt
    assert prompt.endswith(_PROMPT_FIELDS)


def test_validate_injects_fields_when_missing():
    out = validate_template("Describe this bird photo.")
    assert out.endswith("{fields}")
    assert PromptBuilder(template="Describe this bird photo.").render(None).endswith(_PROMPT_FIELDS)


def test_validate_respects_authored_schema_block():
    tpl = (
        "Tag it. Reply with JSON with fields: tags, summary, scene_category, "
        "emotional_tone, cleanup_class, has_text, text_summary, event_hint, significance."
    )
    assert validate_template(tpl) == tpl


@pytest.mark.parametrize(
    ("tpl", "pattern"),
    [
        ("", "template is empty"),
        ("Hello {nope}", r"unknown placeholder\(s\) \{nope\}"),
        ("Hello {context} {bad} {worse}", r"\{bad\}, \{worse\}"),
        ("Unbalanced {context", "malformed placeholder"),
    ],
)
def test_validate_errors(tpl, pattern):
    with pytest.raises(PromptTemplateError, match=pattern):
        validate_template(tpl, source="x.txt")


def test_literal_braces_allowed():
    out = validate_template('Return {{"tags": [...]}} {fields}')
    assert PromptBuilder(template=out).render(None).startswith('Return {"tags": [...]}')


def test_load_prompt_template_missing_and_ok(tmp_path):
    with pytest.raises(PromptTemplateError, match="nope.txt"):
        load_prompt_template(tmp_path / "nope.txt")
    p = tmp_path / "t.txt"
    p.write_text("Custom {fields}", encoding="utf-8")
    assert load_prompt_template(p) == "Custom {fields}"
    p.write_text("Custom {oops}", encoding="utf-8")
    with pytest.raises(PromptTemplateError, match="t.txt"):
        load_prompt_template(p)


# --- prompt subcommand --------------------------------------------------------------


def test_prompt_show_prints_default_template(capsys, monkeypatch):
    monkeypatch.setenv("PYIMGTAG_NO_UPDATE_CHECK", "1")
    assert main(["prompt", "show"]) == 0
    out = capsys.readouterr().out
    assert out.rstrip("\n") == DEFAULT_TEMPLATE.rstrip("\n")
    assert "{fields}" in out and "{vocabulary}" in out


def test_prompt_show_rendered(capsys, monkeypatch):
    monkeypatch.setenv("PYIMGTAG_NO_UPDATE_CHECK", "1")
    assert main(["prompt", "show", "--rendered"]) == 0
    out = capsys.readouterr().out
    assert out.startswith(_PROMPT_BASE)
    assert "{" not in out.replace("{}", "")  # no placeholders left


def test_prompt_without_action_shows_usage(capsys, monkeypatch):
    monkeypatch.setenv("PYIMGTAG_NO_UPDATE_CHECK", "1")
    assert main(["prompt"]) == 1
    assert "Usage: pyimgtag prompt" in capsys.readouterr().err


def test_prompt_show_output_is_a_valid_template_roundtrip(capsys, monkeypatch, tmp_path):
    """`prompt show > f; run --prompt-template f` must reproduce the default prompt."""
    monkeypatch.setenv("PYIMGTAG_NO_UPDATE_CHECK", "1")
    main(["prompt", "show"])
    p = tmp_path / "t.txt"
    p.write_text(capsys.readouterr().out, encoding="utf-8")
    assert PromptBuilder(template=load_prompt_template(p)).render(None) == _PROMPT_BASE


def test_backend_parity_of_post_mapping_layer():
    """Same recorded responses through every backend's extractor + the vocabulary
    yield identical canonical tags — the post-mapping layer is backend-agnostic."""
    from pyimgtag.cloud_clients import AnthropicClient, GeminiClient, OpenAIClient
    from pyimgtag.ollama_client import _parse_response

    body = json.dumps(
        {
            "tags": ["Seaside", "shores", "Hikes", "sunset"],
            "summary": "s",
            "scene_category": "outdoor_leisure",
            "emotional_tone": "positive",
            "cleanup_class": "keep",
            "has_text": False,
            "text_summary": None,
            "event_hint": "outing",
            "significance": "low",
        }
    )
    payloads = {
        "ollama": body,
        "anthropic": AnthropicClient._extract_text(None, {"content": [{"text": body}]}),
        "openai": OpenAIClient._extract_text(None, {"choices": [{"message": {"content": body}}]}),
        "gemini": GeminiClient._extract_text(
            None, {"candidates": [{"content": {"parts": [{"text": body}]}}]}
        ),
    }
    results = {}
    for backend, text in payloads.items():
        v = Vocabulary(tags=["beach", "hiking"], synonyms={"seaside": "beach", "shore": "beach"})
        results[backend] = v.canonicalize(_parse_response(text).tags)
    assert len({tuple(r) for r in results.values()}) == 1, results
    assert results["ollama"] == ["beach", "hikes", "sunset"]
