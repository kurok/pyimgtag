"""Tests for cloud_clients (Anthropic / OpenAI / Gemini vision clients)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import requests
from PIL import Image as PILImage

from pyimgtag.cloud_clients import (
    SUPPORTED_BACKENDS,
    AnthropicClient,
    CloudClientError,
    GeminiClient,
    OpenAIClient,
    make_image_client,
)
from pyimgtag.models import JudgeScores


@pytest.fixture()
def jpg(tmp_path):
    p = tmp_path / "img.jpg"
    PILImage.new("RGB", (40, 40), color=(64, 128, 192)).save(str(p))
    return str(p)


def _judge_payload() -> dict:
    return {
        "impact": 8,
        "story_subject": 7,
        "composition_center": 8,
        "lighting": 7,
        "creativity_style": 6,
        "color_mood": 8,
        "presentation_crop": 7,
        "technical_excellence": 8,
        "focus_sharpness": 9,
        "exposure_tonal": 7,
        "noise_cleanliness": 8,
        "subject_separation": 6,
        "edit_integrity": 7,
        "verdict": "Solid frame.",
    }


def _tag_payload() -> dict:
    return {
        "tags": ["sunset", "beach"],
        "summary": "Sunset over the beach.",
        "scene_category": "outdoor_leisure",
        "emotional_tone": "positive",
        "cleanup_class": "keep",
        "has_text": False,
        "text_summary": None,
        "event_hint": "outing",
        "significance": "medium",
    }


# ---------------------------------------------------------------------------
# API key handling
# ---------------------------------------------------------------------------


class TestApiKeyResolution:
    def test_anthropic_missing_key_raises(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(CloudClientError, match="anthropic"):
            AnthropicClient()

    def test_openai_missing_key_raises(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(CloudClientError, match="openai"):
            OpenAIClient()

    def test_gemini_missing_key_raises(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        with pytest.raises(CloudClientError, match="gemini"):
            GeminiClient()

    def test_anthropic_explicit_key_used(self):
        c = AnthropicClient(api_key="sk-ant-test")
        assert c._session.headers["x-api-key"] == "sk-ant-test"

    def test_openai_bearer_header_set(self):
        c = OpenAIClient(api_key="sk-openai-test")
        assert c._session.headers["Authorization"] == "Bearer sk-openai-test"

    def test_gemini_falls_back_to_GEMINI_API_KEY(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.setenv("GEMINI_API_KEY", "fallback")
        c = GeminiClient()
        assert c._api_key == "fallback"


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------


class TestAnthropicClient:
    def test_judge_parses_response(self, jpg):
        client = AnthropicClient(api_key="x")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "content": [{"type": "text", "text": json.dumps(_judge_payload())}]
        }
        mock_resp.raise_for_status = MagicMock()
        with patch.object(client._session, "post", return_value=mock_resp) as mock_post:
            result = client.judge_image(jpg)
        assert isinstance(result, JudgeScores)
        assert result.impact == 8
        # The request body must include base64 image and text prompt
        sent = mock_post.call_args[1]["json"]
        assert sent["model"] == "claude-sonnet-4-6"
        content = sent["messages"][0]["content"]
        assert content[0]["type"] == "image"
        assert content[0]["source"]["media_type"] == "image/jpeg"
        assert content[1]["type"] == "text"

    def test_tag_parses_response(self, jpg):
        client = AnthropicClient(api_key="x")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "content": [{"type": "text", "text": json.dumps(_tag_payload())}]
        }
        mock_resp.raise_for_status = MagicMock()
        with patch.object(client._session, "post", return_value=mock_resp):
            result = client.tag_image(jpg)
        assert "sunset" in result.tags
        assert result.summary == "Sunset over the beach."

    def test_judge_returns_none_on_request_error(self, jpg):
        client = AnthropicClient(api_key="x")
        with patch.object(client._session, "post", side_effect=requests.RequestException("boom")):
            assert client.judge_image(jpg) is None

    def test_tag_returns_error_on_request_error(self, jpg):
        client = AnthropicClient(api_key="x")
        with patch.object(client._session, "post", side_effect=requests.RequestException("boom")):
            result = client.tag_image(jpg)
        assert "anthropic request failed" in (result.error or "")


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------


class TestOpenAIClient:
    def test_judge_parses_response(self, jpg):
        client = OpenAIClient(api_key="x")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": json.dumps(_judge_payload())}}]
        }
        mock_resp.raise_for_status = MagicMock()
        with patch.object(client._session, "post", return_value=mock_resp) as mock_post:
            result = client.judge_image(jpg)
        assert isinstance(result, JudgeScores)
        assert result.focus_sharpness == 9
        sent = mock_post.call_args[1]["json"]
        assert sent["model"] == "gpt-4o-mini"
        # The image is sent as a data: URL
        content = sent["messages"][0]["content"]
        assert any(isinstance(c, dict) and c.get("type") == "image_url" for c in content)

    def test_tag_returns_error_on_unexpected_shape(self, jpg):
        client = OpenAIClient(api_key="x")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"unexpected": "shape"}
        mock_resp.raise_for_status = MagicMock()
        with patch.object(client._session, "post", return_value=mock_resp):
            result = client.tag_image(jpg)
        assert "openai response shape unexpected" in (result.error or "")


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------


class TestGeminiClient:
    def test_judge_parses_response(self, jpg):
        client = GeminiClient(api_key="g-key")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": json.dumps(_judge_payload())}]}}]
        }
        mock_resp.raise_for_status = MagicMock()
        with patch.object(client._session, "post", return_value=mock_resp) as mock_post:
            result = client.judge_image(jpg)
        assert isinstance(result, JudgeScores)
        assert result.story_subject == 7
        # API key goes in the URL query string for Gemini
        url = mock_post.call_args[0][0]
        assert "key=g-key" in url
        assert "gemini-1.5-flash:generateContent" in url

    def test_judge_returns_none_on_unexpected_shape(self, jpg):
        client = GeminiClient(api_key="g-key")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"candidates": []}
        mock_resp.raise_for_status = MagicMock()
        with patch.object(client._session, "post", return_value=mock_resp):
            assert client.judge_image(jpg) is None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestMakeImageClient:
    def test_supported_backends_listed(self):
        assert set(SUPPORTED_BACKENDS) == {"ollama", "anthropic", "openai", "gemini"}

    def test_ollama_factory(self):
        c = make_image_client("ollama", api_base="http://example:1234")
        from pyimgtag.ollama_client import OllamaClient

        assert isinstance(c, OllamaClient)
        assert c.base_url == "http://example:1234"

    def test_anthropic_factory(self):
        c = make_image_client("anthropic", api_key="sk")
        assert isinstance(c, AnthropicClient)
        assert c.model == "claude-sonnet-4-6"

    def test_openai_factory_with_custom_model(self):
        c = make_image_client("openai", model="gpt-4o", api_key="sk")
        assert isinstance(c, OpenAIClient)
        assert c.model == "gpt-4o"

    def test_gemini_factory_default_base_url(self):
        c = make_image_client("gemini", api_key="g-key")
        assert isinstance(c, GeminiClient)
        assert c.base_url == "https://generativelanguage.googleapis.com"

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError, match="Unknown backend"):
            make_image_client("bogus")
