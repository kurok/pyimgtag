"""Tests for Ollama response parsing (no network calls)."""

from __future__ import annotations

from pyimgtag.ollama_client import (
    _RESPONSE_SCHEMA,
    _build_prompt_with_context,
    _parse_response,
)


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

    def test_new_fields_parsed_correctly(self):
        payload = (
            '{"tags":["cafe","coffee"],"summary":"a cozy cafe",'
            '"scene_category":"indoor_home","emotional_tone":"positive",'
            '"cleanup_class":"keep","has_text":true,"text_summary":"menu board",'
            '"event_hint":"outing","significance":"medium"}'
        )
        r = _parse_response(payload)
        assert r.scene_category == "indoor_home"
        assert r.emotional_tone == "positive"
        assert r.cleanup_class == "keep"
        assert r.has_text is True
        assert r.text_summary == "menu board"
        assert r.event_hint == "outing"
        assert r.significance == "medium"

    def test_invalid_enum_values_return_none(self):
        payload = (
            '{"tags":["test"],"scene_category":"rooftop","emotional_tone":"happy",'
            '"cleanup_class":"maybe","event_hint":"birthday","significance":"ultra"}'
        )
        r = _parse_response(payload)
        assert r.scene_category is None
        assert r.emotional_tone is None
        assert r.cleanup_class is None
        assert r.event_hint is None
        assert r.significance is None

    def test_has_text_defaults_to_false_when_missing(self):
        r = _parse_response('{"tags":["tree"]}')
        assert r.has_text is False
        assert r.text_summary is None

    def test_has_text_false_clears_text_summary(self):
        r = _parse_response('{"tags":["sign"],"has_text":false,"text_summary":"some text"}')
        assert r.has_text is False
        assert r.text_summary is None

    def test_new_fields_absent_when_not_provided(self):
        r = _parse_response('{"tags":["mountain"],"summary":"a peak"}')
        assert r.scene_category is None
        assert r.emotional_tone is None
        assert r.cleanup_class is None
        assert r.has_text is False
        assert r.text_summary is None
        assert r.event_hint is None
        assert r.significance is None


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
        assert "Tag this image" in prompt
        assert "Context" not in prompt

    def test_partial_location(self):
        prompt = _build_prompt_with_context({"city": "Tokyo"})
        assert "Location: Tokyo" in prompt
        assert "- GPS:" not in prompt


class TestResponseSchema:
    def test_schema_has_required_fields(self):
        assert "tags" in _RESPONSE_SCHEMA["properties"]
        assert "summary" in _RESPONSE_SCHEMA["properties"]
        assert "scene_category" in _RESPONSE_SCHEMA["properties"]
        assert "emotional_tone" in _RESPONSE_SCHEMA["properties"]
        assert "cleanup_class" in _RESPONSE_SCHEMA["properties"]
        assert "has_text" in _RESPONSE_SCHEMA["properties"]
        assert "event_hint" in _RESPONSE_SCHEMA["properties"]
        assert "significance" in _RESPONSE_SCHEMA["properties"]

    def test_tags_is_array_of_strings(self):
        tags = _RESPONSE_SCHEMA["properties"]["tags"]
        assert tags["type"] == "array"
        assert tags["items"]["type"] == "string"

    def test_enums_match_validation(self):
        from pyimgtag.ollama_client import (
            _CLEANUP_CLASS_ALLOWED,
            _EMOTIONAL_TONE_ALLOWED,
            _EVENT_HINT_ALLOWED,
            _SCENE_CATEGORY_ALLOWED,
            _SIGNIFICANCE_ALLOWED,
        )

        props = _RESPONSE_SCHEMA["properties"]
        assert set(props["scene_category"]["enum"]) == _SCENE_CATEGORY_ALLOWED
        assert set(props["emotional_tone"]["enum"]) == _EMOTIONAL_TONE_ALLOWED
        assert set(props["cleanup_class"]["enum"]) == _CLEANUP_CLASS_ALLOWED
        assert set(props["event_hint"]["enum"]) == _EVENT_HINT_ALLOWED
        assert set(props["significance"]["enum"]) == _SIGNIFICANCE_ALLOWED
