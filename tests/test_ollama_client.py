"""Tests for Ollama response parsing (no network calls)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from pyimgtag.ollama_client import (
    _PROMPT_BASE,
    _PROMPT_FIELDS,
    _build_prompt_with_context,
    _parse_judge_response,
    _parse_response,
)


class TestParseResponse:
    def test_clean_json(self):
        r = _parse_response('{"tags":["sunset","beach","ocean"]}')
        assert r.tags == ["sunset", "beach", "ocean"]
        assert r.summary is None
        assert r.error is None

    def test_truncated_json_recovered(self):
        """Regression: hitting num_predict mid-value would leave a truncated
        JSON object the regex/extractor couldn't parse, producing an "error"
        row even though the first 80% of the response was perfectly valid.
        The repair pass should now recover the completed fields."""
        text = (
            '{"tags": ["bird", "sky", "rocks"], '
            '"summary": "A black bird perched on rocks against a clear blue sky.", '
            '"scene_category": "outdoor_leisure", '
            '"emotional_tone": "neutral", '
            '"cleanup_class": "keep", '
            '"has_text": false, '
            '"text_summary": null, '
            '"event_hint": "outing", '
            '"signif'  # cut off mid-key
        )
        r = _parse_response(text)
        assert r.error is None
        assert r.tags == ["bird", "sky", "rocks"]
        assert r.summary == "A black bird perched on rocks against a clear blue sky."
        assert r.scene_category == "outdoor_leisure"
        # The unfinished field is just dropped — its enum stays None.
        assert r.significance is None

    def test_unparseable_response_includes_snippet(self):
        """The "Could not parse JSON" error now embeds a prefix of the raw
        response so a user staring at error rows in the review UI can tell
        truncation from prose-only refusals from outright nonsense."""
        r = _parse_response("I cannot process this image because of safety policy.")
        assert r.error is not None
        assert "Could not parse JSON" in r.error
        assert "safety policy" in r.error

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

    def test_json_extracted_when_braces_appear_in_preamble_prose(self):
        """Greedy regex fails when model emits {word} prose before the JSON object."""
        text = (
            "The {image} shows outdoor {scene} with citrus {fruit}.\n"
            '{"tags":["mandarine tree","portugal"],"summary":"A grove.",'
            '"scene_category":"outdoor_leisure","emotional_tone":"positive",'
            '"cleanup_class":"keep","has_text":false,'
            '"event_hint":"outing","significance":"medium"}'
        )
        r = _parse_response(text)
        assert r.error is None
        assert "mandarine tree" in r.tags
        assert r.scene_category == "outdoor_leisure"

    def test_json_extracted_when_thinking_tokens_contain_braces(self):
        """<think>...{x}...</think> preamble before JSON must not break parsing."""
        text = (
            "<think>This {image} shows {citrus fruit} in a grove.</think>\n"
            '{"tags":["citrus","tree","portugal"],"summary":"Mandarine grove.",'
            '"scene_category":"outdoor_leisure","emotional_tone":"positive",'
            '"cleanup_class":"keep","has_text":false,'
            '"event_hint":"outing","significance":"low"}'
        )
        r = _parse_response(text)
        assert r.error is None
        assert "citrus" in r.tags

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


class TestPromptFields:
    """Verify _PROMPT_FIELDS and _PROMPT_BASE contain all required field descriptions."""

    def test_prompt_fields_contains_required_fields(self):
        for field in (
            "tags",
            "summary",
            "scene_category",
            "emotional_tone",
            "cleanup_class",
            "has_text",
            "text_summary",
            "event_hint",
            "significance",
        ):
            assert field in _PROMPT_FIELDS, f"_PROMPT_FIELDS missing field: {field}"

    def test_prompt_fields_contains_enum_values(self):
        from pyimgtag.ollama_client import (
            _CLEANUP_CLASS_ALLOWED,
            _EMOTIONAL_TONE_ALLOWED,
            _EVENT_HINT_ALLOWED,
            _SCENE_CATEGORY_ALLOWED,
            _SIGNIFICANCE_ALLOWED,
        )

        for val in _SCENE_CATEGORY_ALLOWED:
            assert val in _PROMPT_FIELDS, f"scene_category value '{val}' missing from prompt"
        for val in _EMOTIONAL_TONE_ALLOWED:
            assert val in _PROMPT_FIELDS, f"emotional_tone value '{val}' missing from prompt"
        for val in _CLEANUP_CLASS_ALLOWED:
            assert val in _PROMPT_FIELDS, f"cleanup_class value '{val}' missing from prompt"
        for val in _EVENT_HINT_ALLOWED:
            assert val in _PROMPT_FIELDS, f"event_hint value '{val}' missing from prompt"
        for val in _SIGNIFICANCE_ALLOWED:
            assert val in _PROMPT_FIELDS, f"significance value '{val}' missing from prompt"

    def test_prompt_base_includes_fields(self):
        assert "Tag this image" in _PROMPT_BASE
        assert _PROMPT_FIELDS in _PROMPT_BASE


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


class TestParseJudgeResponse:
    def test_valid_json_returns_judge_scores(self):
        import json

        raw = json.dumps(
            {
                "impact": 4,
                "story_subject": 3,
                "composition_center": 5,
                "lighting": 4,
                "creativity_style": 3,
                "color_mood": 4,
                "presentation_crop": 4,
                "technical_excellence": 4,
                "focus_sharpness": 5,
                "exposure_tonal": 4,
                "noise_cleanliness": 3,
                "subject_separation": 4,
                "edit_integrity": 4,
                "verdict": "Strong composition, weak noise.",
            }
        )
        result = _parse_judge_response(raw)
        assert result is not None
        assert result.impact == 4.0
        assert result.focus_sharpness == 5.0
        assert result.verdict == "Strong composition, weak noise."

    def test_missing_verdict_defaults_to_empty(self):
        import json

        raw = json.dumps(
            {
                "impact": 3,
                "story_subject": 3,
                "composition_center": 3,
                "lighting": 3,
                "creativity_style": 3,
                "color_mood": 3,
                "presentation_crop": 3,
                "technical_excellence": 3,
                "focus_sharpness": 3,
                "exposure_tonal": 3,
                "noise_cleanliness": 3,
                "subject_separation": 3,
                "edit_integrity": 3,
            }
        )
        result = _parse_judge_response(raw)
        assert result is not None
        assert result.verdict == ""

    def test_score_clamped_to_1_10(self):
        import json

        raw = json.dumps(
            {
                "impact": 11,  # over the max
                "story_subject": 0,  # under the min
                "composition_center": 5,
                "lighting": 5,
                "creativity_style": 5,
                "color_mood": 5,
                "presentation_crop": 5,
                "technical_excellence": 5,
                "focus_sharpness": 5,
                "exposure_tonal": 5,
                "noise_cleanliness": 5,
                "subject_separation": 5,
                "edit_integrity": 5,
            }
        )
        result = _parse_judge_response(raw)
        assert result is not None
        assert result.impact == 10
        assert result.story_subject == 1

    def test_missing_score_field_defaults_to_5(self):
        import json

        raw = json.dumps(
            {
                "impact": 8,
                "story_subject": 8,
                "composition_center": 8,
                "lighting": 8,
                "creativity_style": 8,
                "color_mood": 8,
                "presentation_crop": 8,
                "technical_excellence": 8,
                "focus_sharpness": 8,
                "exposure_tonal": 8,
                "subject_separation": 8,
                "edit_integrity": 8,
            }
        )
        result = _parse_judge_response(raw)
        assert result is not None
        assert result.noise_cleanliness == 5

    def test_unparseable_returns_none(self):
        assert _parse_judge_response("not json at all") is None


class TestOllamaClientJudgeImage:
    def test_judge_image_returns_judge_scores_on_success(self, tmp_path):
        import json
        from unittest.mock import MagicMock, patch

        from PIL import Image as PILImage

        from pyimgtag.ollama_client import OllamaClient

        img = tmp_path / "photo.jpg"
        PILImage.new("RGB", (100, 100), color=(128, 128, 128)).save(str(img))
        payload = {
            "impact": 4,
            "story_subject": 4,
            "composition_center": 4,
            "lighting": 4,
            "creativity_style": 4,
            "color_mood": 4,
            "presentation_crop": 4,
            "technical_excellence": 4,
            "focus_sharpness": 4,
            "exposure_tonal": 4,
            "noise_cleanliness": 4,
            "subject_separation": 4,
            "edit_integrity": 4,
            "verdict": "Solid neutral image.",
        }
        mock_response = MagicMock()
        mock_response.json.return_value = {"message": {"content": json.dumps(payload)}}
        mock_response.raise_for_status = MagicMock()
        client = OllamaClient()
        with patch.object(client._session, "post", return_value=mock_response):
            result = client.judge_image(str(img))
        assert result is not None
        assert result.impact == 4.0
        assert result.verdict == "Solid neutral image."

    def test_judge_image_returns_none_on_request_error(self, tmp_path):
        from unittest.mock import patch

        import requests as req
        from PIL import Image as PILImage

        from pyimgtag.ollama_client import OllamaClient

        img = tmp_path / "photo.jpg"
        PILImage.new("RGB", (100, 100)).save(str(img))
        client = OllamaClient()
        with patch.object(client._session, "post", side_effect=req.RequestException("down")):
            result = client.judge_image(str(img))
        assert result is None

    def test_judge_image_returns_none_on_image_load_failure(self, tmp_path):
        from unittest.mock import patch

        from pyimgtag.ollama_client import OllamaClient

        fake = tmp_path / "bad.jpg"
        fake.write_bytes(b"\x00" * 10)
        client = OllamaClient()
        with patch("pyimgtag.ollama_client.is_raw", return_value=False):
            with patch("pyimgtag.ollama_client.is_heic", return_value=False):
                with patch("pyimgtag.ollama_client.Image.open", side_effect=OSError("cannot read")):
                    result = client.judge_image(str(fake))
        assert result is None

    def test_judge_image_returns_none_on_response_parse_error(self, tmp_path):
        from unittest.mock import MagicMock, patch

        from PIL import Image as PILImage

        from pyimgtag.ollama_client import OllamaClient

        img = tmp_path / "photo.jpg"
        PILImage.new("RGB", (100, 100), color=(128, 128, 128)).save(str(img))
        mock_response = MagicMock()
        mock_response.json.side_effect = KeyError("missing key")
        mock_response.raise_for_status = MagicMock()
        client = OllamaClient()
        with patch.object(client._session, "post", return_value=mock_response):
            result = client.judge_image(str(img))
        assert result is None


class TestParseJudgeResponseEdgeCases:
    """Additional tests for _parse_judge_response edge cases."""

    def test_non_string_verdict_defaults_to_empty(self):
        import json

        raw = json.dumps(
            {
                "impact": 4,
                "story_subject": 3,
                "composition_center": 4,
                "lighting": 4,
                "creativity_style": 3,
                "color_mood": 4,
                "presentation_crop": 4,
                "technical_excellence": 4,
                "focus_sharpness": 4,
                "exposure_tonal": 4,
                "noise_cleanliness": 3,
                "subject_separation": 4,
                "edit_integrity": 4,
                "verdict": 123,  # integer instead of string
            }
        )
        result = _parse_judge_response(raw)
        assert result is not None
        assert result.verdict == ""

    def test_markdown_fenced_judge_response(self):
        import json

        text = (
            "```json\n"
            + json.dumps(
                {
                    "impact": 4,
                    "story_subject": 3,
                    "composition_center": 4,
                    "lighting": 4,
                    "creativity_style": 3,
                    "color_mood": 4,
                    "presentation_crop": 4,
                    "technical_excellence": 4,
                    "focus_sharpness": 4,
                    "exposure_tonal": 4,
                    "noise_cleanliness": 3,
                    "subject_separation": 4,
                    "edit_integrity": 4,
                    "verdict": "Good overall",
                }
            )
            + "\n```"
        )
        result = _parse_judge_response(text)
        assert result is not None
        assert result.impact == 4.0
        assert result.verdict == "Good overall"

    def test_judge_response_with_text_around_json(self):
        import json

        text = "The photo is excellent. " + json.dumps(
            {
                "impact": 5,
                "story_subject": 4,
                "composition_center": 5,
                "lighting": 4,
                "creativity_style": 4,
                "color_mood": 4,
                "presentation_crop": 4,
                "technical_excellence": 4,
                "focus_sharpness": 4,
                "exposure_tonal": 4,
                "noise_cleanliness": 4,
                "subject_separation": 4,
                "edit_integrity": 4,
                "verdict": "Excellent composition and light.",
            }
        )
        result = _parse_judge_response(text)
        assert result is not None
        assert result.impact == 5.0

    def test_judge_response_missing_all_scores_defaults_to_5(self):
        import json

        raw = json.dumps({"verdict": "No scores provided"})
        result = _parse_judge_response(raw)
        assert result is not None
        assert result.impact == 5
        assert result.story_subject == 5
        assert result.composition_center == 5

    def test_judge_response_string_scores_converted_to_int(self):
        import json

        raw = json.dumps(
            {
                "impact": "9",
                "story_subject": "6",
                "composition_center": 8,
                "lighting": 8,
                "creativity_style": 6,
                "color_mood": 8,
                "presentation_crop": 8,
                "technical_excellence": 8,
                "focus_sharpness": 8,
                "exposure_tonal": 8,
                "noise_cleanliness": 6,
                "subject_separation": 8,
                "edit_integrity": 8,
                "verdict": "Good",
            }
        )
        result = _parse_judge_response(raw)
        assert result is not None
        assert result.impact == 9
        assert result.story_subject == 6
