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


class TestRetryAfterParsing:
    def test_delta_seconds_and_http_date(self):
        from datetime import datetime, timedelta, timezone
        from email.utils import format_datetime

        from pyimgtag.cloud_clients import _parse_retry_after

        assert _parse_retry_after("3") == 3.0
        assert _parse_retry_after(" 2.5 ") == 2.5
        assert _parse_retry_after("-4") == 0.0
        assert _parse_retry_after(None) is None
        assert _parse_retry_after("") is None
        assert _parse_retry_after("soon") is None

        soon = datetime.now(timezone.utc) + timedelta(seconds=30)
        parsed = _parse_retry_after(format_datetime(soon))
        assert parsed is not None and 25 <= parsed <= 31

    def test_backoff_is_exponential_capped_and_jittered(self):
        from pyimgtag.cloud_clients import _BACKOFF_CAP, _backoff_delay

        for attempt in range(1, 8):
            base = min(_BACKOFF_CAP, 2 ** (attempt - 1))
            delay = _backoff_delay(attempt)
            assert base <= delay <= base * 1.25
        # A server-supplied delay wins outright (still capped).
        assert _backoff_delay(1, 7.0) == 7.0
        assert _backoff_delay(1, 900.0) == _BACKOFF_CAP


def _limited(status: int = 429, retry_after: str | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {"Retry-After": retry_after} if retry_after else {}
    return resp


def _ok_tag_response() -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {}
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"content": [{"type": "text", "text": json.dumps(_tag_payload())}]}
    return resp


class TestRateLimitRetries:
    def test_429_with_retry_after_backs_off_then_succeeds(self, jpg):
        client = AnthropicClient(api_key="x")
        delays: list[float] = []
        with (
            patch.object(
                client._session, "post", side_effect=[_limited(429, "1"), _ok_tag_response()]
            ) as post,
            patch("pyimgtag.cloud_clients.time.sleep", side_effect=delays.append),
        ):
            result = client.tag_image(jpg)
        assert post.call_count == 2
        assert delays == [1.0]  # Retry-After honoured verbatim
        assert "sunset" in result.tags

    def test_503_is_retried_too(self, jpg):
        client = AnthropicClient(api_key="x")
        with (
            patch.object(
                client._session, "post", side_effect=[_limited(503), _ok_tag_response()]
            ) as post,
            patch("pyimgtag.cloud_clients.time.sleep"),
        ):
            result = client.tag_image(jpg)
        assert post.call_count == 2
        assert result.error is None

    def test_sustained_429_fails_the_image_in_bounded_time(self, jpg):
        from pyimgtag.cloud_clients import _MAX_ATTEMPTS

        client = AnthropicClient(api_key="x")
        delays: list[float] = []
        with (
            patch.object(client._session, "post", return_value=_limited(429)) as post,
            patch("pyimgtag.cloud_clients.time.sleep", side_effect=delays.append),
        ):
            result = client.tag_image(jpg)
        assert post.call_count == _MAX_ATTEMPTS
        assert len(delays) == _MAX_ATTEMPTS - 1
        assert delays == sorted(delays)  # exponential, never shrinking
        for attempt, delay in enumerate(delays, start=1):
            base = 2 ** (attempt - 1)
            assert base <= delay <= base * 1.25
        assert "rate limited (HTTP 429)" in (result.error or "")

    def test_sustained_429_returns_none_on_the_judge_path(self, jpg):
        client = AnthropicClient(api_key="x")
        with (
            patch.object(client._session, "post", return_value=_limited(429)),
            patch("pyimgtag.cloud_clients.time.sleep"),
        ):
            assert client.judge_image(jpg) is None

    def test_max_rps_bucket_is_acquired_per_request(self, jpg):
        from pyimgtag import concurrent_pipeline

        client = AnthropicClient(api_key="x")
        concurrent_pipeline.set_global_rate_limit(1000.0)
        limiter = concurrent_pipeline.get_global_rate_limit()
        assert limiter is not None
        with patch.object(limiter, "acquire", wraps=limiter.acquire) as acquire:
            with patch.object(client._session, "post", return_value=_ok_tag_response()):
                client.tag_image(jpg)
        assert acquire.call_count == 1
