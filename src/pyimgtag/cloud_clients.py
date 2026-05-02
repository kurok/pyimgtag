"""Cloud vision-model clients for pyimgtag.

Provides Anthropic, OpenAI, and Gemini client classes with the same public
shape as :class:`pyimgtag.ollama_client.OllamaClient`:

- ``__init__(model, max_dim, timeout, api_key=None, base_url=None)``
- ``tag_image(file_path, context=None) -> TagResult``
- ``judge_image(file_path) -> JudgeScores | None``
- ``close()``

Each client routes the same JPEG bytes through the provider's vision API,
asks for a strict JSON response, and feeds the returned text into the
existing :func:`_parse_response` / :func:`_parse_judge_response` helpers in
:mod:`pyimgtag.ollama_client`. The prompt content is identical across
backends, so judge scores and tag schemas remain comparable.

API key resolution: each client either takes ``api_key=...`` explicitly or
reads its provider-conventional environment variable (e.g.
``ANTHROPIC_API_KEY``, ``OPENAI_API_KEY``, ``GOOGLE_API_KEY``).
"""

from __future__ import annotations

import os
from typing import Any

import requests

from pyimgtag.models import JudgeScores, TagResult
from pyimgtag.ollama_client import (
    _JUDGE_PROMPT,
    _PROMPT_BASE,
    _build_prompt_with_context,
    _parse_judge_response,
    _parse_response,
    prepare_image_b64,
)

# Sensible defaults for each provider. Users can override with --model.
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_GEMINI_MODEL = "gemini-1.5-flash"

DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com"
DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com"

_ANTHROPIC_API_VERSION = "2023-06-01"

# Token budget for the JSON response. Slightly larger than Ollama's default
# because cloud models pad JSON with whitespace.
_CLOUD_MAX_TOKENS = 1024


class CloudClientError(RuntimeError):
    """Raised when the user has not configured an API key for the chosen backend."""


def _require_api_key(explicit: str | None, env_var: str, backend: str) -> str:
    key = explicit or os.environ.get(env_var, "").strip()
    if not key:
        raise CloudClientError(
            f"No API key for backend '{backend}'. "
            f"Set the {env_var} environment variable or pass --api-key."
        )
    return key


class AnthropicClient:
    """Vision client for Anthropic's Claude API (``/v1/messages``)."""

    def __init__(
        self,
        model: str = DEFAULT_ANTHROPIC_MODEL,
        max_dim: int = 1280,
        timeout: int = 120,
        api_key: str | None = None,
        base_url: str = DEFAULT_ANTHROPIC_BASE_URL,
    ) -> None:
        self.model = model
        self.max_dim = max_dim
        self.timeout = timeout
        self.base_url = base_url.rstrip("/")
        self._api_key = _require_api_key(api_key, "ANTHROPIC_API_KEY", "anthropic")
        self._session = requests.Session()
        self._session.headers.update(
            {
                "x-api-key": self._api_key,
                "anthropic-version": _ANTHROPIC_API_VERSION,
                "content-type": "application/json",
            }
        )

    def tag_image(self, file_path: str, context: dict | None = None) -> TagResult:
        try:
            img_b64 = prepare_image_b64(file_path, self.max_dim)
        except (OSError, ValueError, RuntimeError) as e:
            return TagResult(error=f"Image load failed: {e}")
        prompt = _build_prompt_with_context(context) if context else _PROMPT_BASE
        text = self._call(prompt, img_b64, on_error_msg="anthropic request failed")
        if isinstance(text, TagResult):
            return text
        if text is None:
            return TagResult(error="empty response")
        return _parse_response(text)

    def judge_image(self, file_path: str) -> JudgeScores | None:
        try:
            img_b64 = prepare_image_b64(file_path, self.max_dim)
        except (OSError, ValueError, RuntimeError):
            return None
        text = self._call(_JUDGE_PROMPT, img_b64, on_error_msg=None)
        if not isinstance(text, str):
            return None
        return _parse_judge_response(text)

    def _call(
        self,
        prompt: str,
        img_b64: str,
        *,
        on_error_msg: str | None,
    ) -> str | TagResult | None:
        payload = {
            "model": self.model,
            "max_tokens": _CLOUD_MAX_TOKENS,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": img_b64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        }
        try:
            resp = self._session.post(
                f"{self.base_url}/v1/messages",
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            return TagResult(error=f"{on_error_msg}: {e}") if on_error_msg else None
        try:
            return data["content"][0]["text"]
        except (KeyError, IndexError, TypeError):
            return TagResult(error="anthropic response shape unexpected") if on_error_msg else None

    def close(self) -> None:
        self._session.close()


class OpenAIClient:
    """Vision client for OpenAI's chat completions API."""

    def __init__(
        self,
        model: str = DEFAULT_OPENAI_MODEL,
        max_dim: int = 1280,
        timeout: int = 120,
        api_key: str | None = None,
        base_url: str = DEFAULT_OPENAI_BASE_URL,
    ) -> None:
        self.model = model
        self.max_dim = max_dim
        self.timeout = timeout
        self.base_url = base_url.rstrip("/")
        self._api_key = _require_api_key(api_key, "OPENAI_API_KEY", "openai")
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            }
        )

    def tag_image(self, file_path: str, context: dict | None = None) -> TagResult:
        try:
            img_b64 = prepare_image_b64(file_path, self.max_dim)
        except (OSError, ValueError, RuntimeError) as e:
            return TagResult(error=f"Image load failed: {e}")
        prompt = _build_prompt_with_context(context) if context else _PROMPT_BASE
        text = self._call(prompt, img_b64, on_error_msg="openai request failed")
        if isinstance(text, TagResult):
            return text
        if text is None:
            return TagResult(error="empty response")
        return _parse_response(text)

    def judge_image(self, file_path: str) -> JudgeScores | None:
        try:
            img_b64 = prepare_image_b64(file_path, self.max_dim)
        except (OSError, ValueError, RuntimeError):
            return None
        text = self._call(_JUDGE_PROMPT, img_b64, on_error_msg=None)
        if not isinstance(text, str):
            return None
        return _parse_judge_response(text)

    def _call(
        self,
        prompt: str,
        img_b64: str,
        *,
        on_error_msg: str | None,
    ) -> str | TagResult | None:
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": _CLOUD_MAX_TOKENS,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
                        },
                    ],
                }
            ],
        }
        try:
            resp = self._session.post(
                f"{self.base_url}/v1/chat/completions",
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            return TagResult(error=f"{on_error_msg}: {e}") if on_error_msg else None
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return TagResult(error="openai response shape unexpected") if on_error_msg else None

    def close(self) -> None:
        self._session.close()


class GeminiClient:
    """Vision client for Google's Gemini API (``generateContent``)."""

    def __init__(
        self,
        model: str = DEFAULT_GEMINI_MODEL,
        max_dim: int = 1280,
        timeout: int = 120,
        api_key: str | None = None,
        base_url: str = DEFAULT_GEMINI_BASE_URL,
    ) -> None:
        self.model = model
        self.max_dim = max_dim
        self.timeout = timeout
        self.base_url = base_url.rstrip("/")
        self._api_key = _resolve_gemini_key(api_key)
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})

    def tag_image(self, file_path: str, context: dict | None = None) -> TagResult:
        try:
            img_b64 = prepare_image_b64(file_path, self.max_dim)
        except (OSError, ValueError, RuntimeError) as e:
            return TagResult(error=f"Image load failed: {e}")
        prompt = _build_prompt_with_context(context) if context else _PROMPT_BASE
        text = self._call(prompt, img_b64, on_error_msg="gemini request failed")
        if isinstance(text, TagResult):
            return text
        if text is None:
            return TagResult(error="empty response")
        return _parse_response(text)

    def judge_image(self, file_path: str) -> JudgeScores | None:
        try:
            img_b64 = prepare_image_b64(file_path, self.max_dim)
        except (OSError, ValueError, RuntimeError):
            return None
        text = self._call(_JUDGE_PROMPT, img_b64, on_error_msg=None)
        if not isinstance(text, str):
            return None
        return _parse_judge_response(text)

    def _call(
        self,
        prompt: str,
        img_b64: str,
        *,
        on_error_msg: str | None,
    ) -> str | TagResult | None:
        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "inline_data": {
                                "mime_type": "image/jpeg",
                                "data": img_b64,
                            }
                        },
                        {"text": prompt},
                    ]
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "maxOutputTokens": _CLOUD_MAX_TOKENS,
            },
        }
        url = f"{self.base_url}/v1beta/models/{self.model}:generateContent?key={self._api_key}"
        try:
            resp = self._session.post(url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            return TagResult(error=f"{on_error_msg}: {e}") if on_error_msg else None
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError):
            return TagResult(error="gemini response shape unexpected") if on_error_msg else None

    def close(self) -> None:
        self._session.close()


def _resolve_gemini_key(explicit: str | None) -> str:
    """Gemini accepts both GOOGLE_API_KEY and GEMINI_API_KEY in the wild."""
    if explicit:
        return explicit
    for var in ("GOOGLE_API_KEY", "GEMINI_API_KEY"):
        val = os.environ.get(var, "").strip()
        if val:
            return val
    raise CloudClientError(
        "No API key for backend 'gemini'. Set GOOGLE_API_KEY (or GEMINI_API_KEY) or pass --api-key."
    )


def make_image_client(
    backend: str,
    *,
    model: str | None = None,
    max_dim: int = 1280,
    timeout: int = 120,
    api_key: str | None = None,
    api_base: str | None = None,
) -> Any:
    """Build a vision-model client for *backend*.

    Args:
        backend: One of ``"ollama"``, ``"anthropic"``, ``"openai"``, ``"gemini"``.
        model: Model name. ``None`` falls back to the per-backend default.
        max_dim: Max image dimension before sending.
        timeout: HTTP timeout in seconds.
        api_key: Explicit API key (cloud backends only). ``None`` reads the
            provider-conventional environment variable.
        api_base: Override the base URL. For ``ollama`` this is the full
            Ollama URL (default ``http://localhost:11434``); for cloud
            backends it points at the API endpoint root.

    Returns:
        A client object exposing ``tag_image`` and ``judge_image``.

    Raises:
        ValueError: If *backend* is unrecognised.
        CloudClientError: If a cloud backend is selected without an API key.
    """
    backend_norm = backend.lower().strip()
    if backend_norm == "ollama":
        from pyimgtag.ollama_client import OllamaClient

        return OllamaClient(
            model=model or "gemma4:e4b",
            base_url=api_base or "http://localhost:11434",
            max_dim=max_dim,
            timeout=timeout,
        )
    if backend_norm == "anthropic":
        return AnthropicClient(
            model=model or DEFAULT_ANTHROPIC_MODEL,
            max_dim=max_dim,
            timeout=timeout,
            api_key=api_key,
            base_url=api_base or DEFAULT_ANTHROPIC_BASE_URL,
        )
    if backend_norm == "openai":
        return OpenAIClient(
            model=model or DEFAULT_OPENAI_MODEL,
            max_dim=max_dim,
            timeout=timeout,
            api_key=api_key,
            base_url=api_base or DEFAULT_OPENAI_BASE_URL,
        )
    if backend_norm == "gemini":
        return GeminiClient(
            model=model or DEFAULT_GEMINI_MODEL,
            max_dim=max_dim,
            timeout=timeout,
            api_key=api_key,
            base_url=api_base or DEFAULT_GEMINI_BASE_URL,
        )
    raise ValueError(
        f"Unknown backend {backend!r}; expected one of ollama, anthropic, openai, gemini"
    )


SUPPORTED_BACKENDS: tuple[str, ...] = ("ollama", "anthropic", "openai", "gemini")
