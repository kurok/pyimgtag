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
    return {"score": 8, "verdict": "Solid frame."}


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
        assert result.score == 8
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

    def test_tag_returns_error_on_unexpected_shape(self, jpg):
        client = AnthropicClient(api_key="x")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"type": "error", "error": {"message": "overloaded"}}
        mock_resp.raise_for_status = MagicMock()
        with patch.object(client._session, "post", return_value=mock_resp):
            result = client.tag_image(jpg)
        # The fixed prefix is preserved and a snippet of the real payload is
        # appended so the user can see what the provider actually returned.
        assert "anthropic response shape unexpected" in (result.error or "")
        assert "overloaded" in (result.error or "")

    def test_tag_returns_error_on_image_load_failure(self, jpg):
        client = AnthropicClient(api_key="x")
        with patch("pyimgtag.cloud_clients.prepare_image_b64", side_effect=OSError("read error")):
            result = client.tag_image(jpg)
        assert result.error is not None
        assert "Image load failed" in result.error

    def test_tag_returns_empty_response_when_text_none(self, jpg):
        # _call returns a literal None text (provider sent text: null) -> the
        # tag_image path reports "empty response".
        client = AnthropicClient(api_key="x")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"content": [{"type": "text", "text": None}]}
        mock_resp.raise_for_status = MagicMock()
        with patch.object(client._session, "post", return_value=mock_resp):
            result = client.tag_image(jpg)
        assert result.error == "empty response"

    def test_judge_returns_none_on_image_load_failure(self, jpg):
        client = AnthropicClient(api_key="x")
        with patch("pyimgtag.cloud_clients.prepare_image_b64", side_effect=OSError("read error")):
            assert client.judge_image(jpg) is None


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
        assert result.score == 8
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

    def test_tag_parses_response(self, jpg):
        client = OpenAIClient(api_key="x")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": json.dumps(_tag_payload())}}]
        }
        mock_resp.raise_for_status = MagicMock()
        with patch.object(client._session, "post", return_value=mock_resp):
            result = client.tag_image(jpg)
        assert "sunset" in result.tags
        assert result.summary == "Sunset over the beach."

    def test_tag_returns_error_on_image_load_failure(self, jpg):
        client = OpenAIClient(api_key="x")
        with patch("pyimgtag.cloud_clients.prepare_image_b64", side_effect=OSError("read error")):
            result = client.tag_image(jpg)
        assert "Image load failed" in (result.error or "")

    def test_tag_returns_empty_response_when_text_none(self, jpg):
        client = OpenAIClient(api_key="x")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"choices": [{"message": {"content": None}}]}
        mock_resp.raise_for_status = MagicMock()
        with patch.object(client._session, "post", return_value=mock_resp):
            result = client.tag_image(jpg)
        assert result.error == "empty response"

    def test_tag_returns_error_on_request_error(self, jpg):
        # _call catches RequestException and returns a TagResult on the tag path.
        client = OpenAIClient(api_key="x")
        with patch.object(client._session, "post", side_effect=requests.RequestException("boom")):
            result = client.tag_image(jpg)
        assert "openai request failed" in (result.error or "")

    def test_judge_returns_none_on_image_load_failure(self, jpg):
        client = OpenAIClient(api_key="x")
        with patch("pyimgtag.cloud_clients.prepare_image_b64", side_effect=OSError("read error")):
            assert client.judge_image(jpg) is None

    def test_judge_returns_none_on_request_error(self, jpg):
        # _call with on_error_msg=None (judge path) returns None on request error,
        # so judge_image surfaces None.
        client = OpenAIClient(api_key="x")
        with patch.object(client._session, "post", side_effect=requests.RequestException("boom")):
            assert client.judge_image(jpg) is None

    def test_judge_returns_none_on_unexpected_shape(self, jpg):
        # _call returns None (not a str) when the shape is unexpected on the judge
        # path -> judge_image returns None (line 274).
        client = OpenAIClient(api_key="x")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"unexpected": "shape"}
        mock_resp.raise_for_status = MagicMock()
        with patch.object(client._session, "post", return_value=mock_resp):
            assert client.judge_image(jpg) is None


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
        assert result.score == 8
        # API key travels in the x-goog-api-key header, never in the URL
        url = mock_post.call_args[0][0]
        assert "g-key" not in url
        assert client._session.headers["x-goog-api-key"] == "g-key"
        assert "gemini-1.5-flash:generateContent" in url

    def test_judge_returns_none_on_unexpected_shape(self, jpg):
        client = GeminiClient(api_key="g-key")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"candidates": []}
        mock_resp.raise_for_status = MagicMock()
        with patch.object(client._session, "post", return_value=mock_resp):
            assert client.judge_image(jpg) is None

    def test_tag_parses_response(self, jpg):
        client = GeminiClient(api_key="g-key")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": json.dumps(_tag_payload())}]}}]
        }
        mock_resp.raise_for_status = MagicMock()
        with patch.object(client._session, "post", return_value=mock_resp):
            result = client.tag_image(jpg)
        assert "sunset" in result.tags
        assert result.summary == "Sunset over the beach."

    def test_tag_returns_error_on_image_load_failure(self, jpg):
        client = GeminiClient(api_key="g-key")
        with patch("pyimgtag.cloud_clients.prepare_image_b64", side_effect=OSError("read error")):
            result = client.tag_image(jpg)
        assert result.error is not None
        assert "Image load failed" in result.error

    def test_tag_returns_error_on_request_error(self, jpg):
        # _call catches RequestException; tag path returns a TagResult (line 371,
        # plus _call lines 432-433).
        client = GeminiClient(api_key="g-key")
        with patch.object(client._session, "post", side_effect=requests.RequestException("boom")):
            result = client.tag_image(jpg)
        assert "gemini request failed" in (result.error or "")

    def test_tag_returns_error_on_unexpected_shape(self, jpg):
        client = GeminiClient(api_key="g-key")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"candidates": []}
        mock_resp.raise_for_status = MagicMock()
        with patch.object(client._session, "post", return_value=mock_resp):
            result = client.tag_image(jpg)
        assert "gemini response shape unexpected" in (result.error or "")

    def test_http_error_message_does_not_leak_api_key(self, jpg):
        # Regression: HTTPError stringifies with the full request URL; the API
        # key must not be in the URL, so it must not end up in TagResult.error
        # (which is persisted to the progress DB and JSON/CSV output).
        client = GeminiClient(api_key="g-key")

        def fake_post(url, json=None, timeout=None):
            resp = requests.Response()
            resp.status_code = 403
            resp.reason = "Forbidden"
            resp.url = url
            return resp

        with patch.object(client._session, "post", side_effect=fake_post):
            result = client.tag_image(jpg)
        assert "gemini request failed" in (result.error or "")
        assert "403" in (result.error or "")
        assert "g-key" not in (result.error or "")

    def test_tag_returns_empty_response_when_text_none(self, jpg):
        # text key present but null -> _call returns None -> "empty response" (line 373).
        client = GeminiClient(api_key="g-key")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"candidates": [{"content": {"parts": [{"text": None}]}}]}
        mock_resp.raise_for_status = MagicMock()
        with patch.object(client._session, "post", return_value=mock_resp):
            result = client.tag_image(jpg)
        assert result.error == "empty response"

    def test_judge_returns_none_on_image_load_failure(self, jpg):
        client = GeminiClient(api_key="g-key")
        with patch("pyimgtag.cloud_clients.prepare_image_b64", side_effect=OSError("read error")):
            assert client.judge_image(jpg) is None

    def test_judge_returns_none_on_request_error(self, jpg):
        client = GeminiClient(api_key="g-key")
        with patch.object(client._session, "post", side_effect=requests.RequestException("boom")):
            assert client.judge_image(jpg) is None


# ---------------------------------------------------------------------------
# close() method coverage
# ---------------------------------------------------------------------------


class TestClientClose:
    def test_anthropic_close_does_not_raise(self):
        client = AnthropicClient(api_key="x")
        client.close()  # must not raise

    def test_openai_close_does_not_raise(self):
        client = OpenAIClient(api_key="x")
        client.close()

    def test_gemini_close_does_not_raise(self):
        client = GeminiClient(api_key="g-key")
        client.close()


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
