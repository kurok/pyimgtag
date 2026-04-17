"""Tests for Ollama response parsing (no network calls)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

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

    def test_has_text_integer_one_treated_as_true(self):
        """Model returning integer 1 for has_text must set has_text=True and keep text_summary."""
        r = _parse_response('{"tags":["sign"],"has_text":1,"text_summary":"STOP"}')
        assert r.has_text is True
        assert r.text_summary == "STOP"

    def test_has_text_integer_zero_treated_as_false(self):
        """Model returning integer 0 for has_text must set has_text=False and clear text_summary."""
        r = _parse_response('{"tags":["road"],"has_text":0,"text_summary":"some text"}')
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


class TestPrepareImageRaw:
    """Tests for RAW file handling in OllamaClient._prepare_image."""

    def _make_client(self):
        from pyimgtag.ollama_client import OllamaClient

        return OllamaClient(base_url="http://localhost:11434", model="test")

    def _jpeg_bytes(self) -> bytes:
        import io as _io

        from PIL import Image as _Image

        buf = _io.BytesIO()
        img = _Image.new("RGB", (10, 10), color=(128, 64, 32))
        img.save(buf, format="JPEG")
        return buf.getvalue()

    def test_raw_file_uses_extract_thumbnail(self, tmp_path):
        jpeg_path = tmp_path / "IMG_001_thumb.jpg"
        jpeg_path.write_bytes(self._jpeg_bytes())
        client = self._make_client()
        with patch("pyimgtag.ollama_client.is_raw", return_value=True):
            with patch(
                "pyimgtag.ollama_client.extract_raw_thumbnail", return_value=jpeg_path
            ) as mock_extract:
                with patch("pyimgtag.ollama_client.is_heic", return_value=False):
                    result = client._prepare_image(str(tmp_path / "IMG_001.cr2"))
        assert isinstance(result, str) and len(result) > 0
        mock_extract.assert_called_once()

    def test_raw_falls_back_to_rawpy_when_exiftool_fails(self, tmp_path):
        jpeg_path = tmp_path / "IMG_001_raw.jpg"
        jpeg_path.write_bytes(self._jpeg_bytes())
        client = self._make_client()
        with patch("pyimgtag.ollama_client.is_raw", return_value=True):
            with patch(
                "pyimgtag.ollama_client.extract_raw_thumbnail",
                side_effect=RuntimeError("no embedded JPEG"),
            ):
                with patch("pyimgtag.ollama_client.rawpy_available", return_value=True):
                    with patch(
                        "pyimgtag.ollama_client.convert_raw_with_rawpy",
                        return_value=jpeg_path,
                    ) as mock_rawpy:
                        with patch("pyimgtag.ollama_client.is_heic", return_value=False):
                            result = client._prepare_image(str(tmp_path / "IMG_001.cr2"))
        assert isinstance(result, str) and len(result) > 0
        mock_rawpy.assert_called_once()

    def test_raw_raises_when_both_backends_fail(self, tmp_path):
        fake_cr2 = tmp_path / "IMG_001.cr2"
        fake_cr2.write_bytes(b"\x00" * 100)
        client = self._make_client()
        with patch("pyimgtag.ollama_client.is_raw", return_value=True):
            with patch(
                "pyimgtag.ollama_client.extract_raw_thumbnail",
                side_effect=RuntimeError("no embedded JPEG"),
            ):
                with patch("pyimgtag.ollama_client.rawpy_available", return_value=False):
                    with patch("pyimgtag.ollama_client.is_heic", return_value=False):
                        with pytest.raises(RuntimeError, match="no embedded JPEG"):
                            client._prepare_image(str(fake_cr2))

    def test_non_raw_file_skips_raw_path(self, tmp_path):
        jpeg = tmp_path / "photo.jpg"
        jpeg.write_bytes(self._jpeg_bytes())
        client = self._make_client()
        with patch("pyimgtag.ollama_client.extract_raw_thumbnail") as mock_extract:
            client._prepare_image(str(jpeg))
        mock_extract.assert_not_called()

    def test_image_open_oserror_propagates(self, tmp_path):
        fake = tmp_path / "photo.jpg"
        fake.write_bytes(self._jpeg_bytes())
        client = self._make_client()
        with patch("pyimgtag.ollama_client.is_raw", return_value=False):
            with patch("pyimgtag.ollama_client.is_heic", return_value=False):
                with patch("pyimgtag.ollama_client.Image.open", side_effect=OSError("disk error")):
                    with pytest.raises(OSError, match="disk error"):
                        client._prepare_image(str(fake))

    def test_tag_image_returns_error_on_image_load_failure(self, tmp_path):
        fake = tmp_path / "bad.jpg"
        fake.write_bytes(b"\x00" * 10)
        client = self._make_client()
        with patch("pyimgtag.ollama_client.is_raw", return_value=False):
            with patch("pyimgtag.ollama_client.is_heic", return_value=False):
                with patch("pyimgtag.ollama_client.Image.open", side_effect=OSError("cannot read")):
                    result = client.tag_image(str(fake))
        assert result.error is not None
        assert "Image load failed" in result.error

    def test_close_runs_without_error(self):
        client = self._make_client()
        client.close()  # must not raise

    def test_heic_sips_unavailable_raises(self, tmp_path):
        fake = tmp_path / "photo.heic"
        fake.write_bytes(b"\x00" * 10)
        client = self._make_client()
        with patch("pyimgtag.ollama_client.is_heic", return_value=True):
            with patch("pyimgtag.ollama_client.sips_available", return_value=False):
                with patch("pyimgtag.ollama_client.is_raw", return_value=False):
                    with patch(
                        "pyimgtag.ollama_client.Image.open",
                        side_effect=OSError("not a JPEG"),
                    ):
                        with pytest.raises(OSError):
                            client._prepare_image(str(fake))
