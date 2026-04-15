"""Tests for Ollama response parsing (no network calls)."""

from __future__ import annotations

from pyimgtag.ollama_client import _build_prompt_with_context, _parse_response


class TestParseResponse:
    def test_clean_json(self):
        r = _parse_response('{"tags":["sunset","beach","ocean"]}')
        assert r.tags == ["sunset", "beach", "ocean"]
        assert r.summary is None
        assert r.error is None

    def test_markdown_fenced(self):
        text = '```json\n{"tags":["dog","park"]}\n```'
        r = _parse_response(text)
        assert r.tags == ["dog", "park"]

    def test_text_around_json(self):
        text = 'Here is the result: {"tags":["cat"]} hope this helps!'
        r = _parse_response(text)
        assert r.tags == ["cat"]

    def test_max_five_tags(self):
        r = _parse_response('{"tags":["a","b","c","d","e","f","g"]}')
        assert len(r.tags) == 5

    def test_tags_lowercased(self):
        r = _parse_response('{"tags":["Sunset","BEACH"]}')
        assert r.tags == ["sunset", "beach"]

    def test_no_json(self):
        r = _parse_response("I cannot identify this image")
        assert r.error is not None
        assert r.tags == []

    def test_empty_tags(self):
        r = _parse_response('{"tags":[]}')
        assert r.tags == []
        assert r.summary is None

    def test_missing_summary(self):
        r = _parse_response('{"tags":["tree"]}')
        assert r.tags == ["tree"]
        assert r.summary is None

    def test_non_list_tags(self):
        r = _parse_response('{"tags":"sunset"}')
        assert r.tags == []

    def test_summary_still_parsed_if_present(self):
        r = _parse_response('{"tags":["dog"],"summary":"a happy dog"}')
        assert r.tags == ["dog"]
        assert r.summary == "a happy dog"


class TestBuildPromptWithContext:
    def test_includes_date(self):
        prompt = _build_prompt_with_context({"date": "2026-01-15 10:30:00"})
        assert "Date: 2026-01-15 10:30:00" in prompt

    def test_includes_location(self):
        prompt = _build_prompt_with_context({"city": "Paris", "country": "France"})
        assert "Location: Paris, France" in prompt

    def test_includes_gps(self):
        prompt = _build_prompt_with_context({"lat": 48.8566, "lon": 2.3522})
        assert "GPS: 48.8566, 2.3522" in prompt

    def test_skips_none_fields(self):
        prompt = _build_prompt_with_context({"date": "2026-01-15", "city": None})
        assert "Date: 2026-01-15" in prompt
        assert "Location" not in prompt

    def test_empty_dict_returns_base(self):
        prompt = _build_prompt_with_context({})
        assert "Return compact JSON only." in prompt
        assert "Context" not in prompt

    def test_partial_location(self):
        prompt = _build_prompt_with_context({"city": "Tokyo"})
        assert "Location: Tokyo" in prompt
        assert "- GPS:" not in prompt
