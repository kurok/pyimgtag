"""Tests for Ollama response parsing (no network calls)."""

from __future__ import annotations

from pyimgtag.ollama_client import _parse_response


class TestParseResponse:
    def test_clean_json(self):
        r = _parse_response('{"tags":["sunset","beach","ocean"],"summary":"sunset at beach"}')
        assert r.tags == ["sunset", "beach", "ocean"]
        assert r.summary == "sunset at beach"
        assert r.error is None

    def test_markdown_fenced(self):
        text = '```json\n{"tags":["dog","park"],"summary":"dog in park"}\n```'
        r = _parse_response(text)
        assert r.tags == ["dog", "park"]

    def test_text_around_json(self):
        text = 'Here is the result: {"tags":["cat"],"summary":"a cat"} hope this helps!'
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
        r = _parse_response('{"tags":[],"summary":"nothing"}')
        assert r.tags == []
        assert r.summary == "nothing"

    def test_missing_summary(self):
        r = _parse_response('{"tags":["tree"]}')
        assert r.tags == ["tree"]
        assert r.summary is None

    def test_non_list_tags(self):
        r = _parse_response('{"tags":"sunset"}')
        assert r.tags == []
