"""Cloud vision-model clients for pyimgtag.

Provides Anthropic, OpenAI, and Gemini client classes that share the same
method surface as :class:`pyimgtag.ollama_client.OllamaClient`:

- ``tag_image(file_path, context=None) -> TagResult``
- ``judge_image(file_path) -> JudgeScores | None``
- ``close()``

Constructors differ: cloud clients take ``api_key`` (default: the provider's
environment variable) and ``base_url`` (default: the provider's endpoint),
while ``OllamaClient`` takes ``base_url`` and no ``api_key``.

Each client routes the same JPEG bytes through the provider's vision API,
asks for a strict JSON response, and feeds the returned text into the
existing :func:`_parse_response` / :func:`_parse_judge_response` helpers in
:mod:`pyimgtag.ollama_client`. The prompt content is identical across
backends, so judge scores and tag schemas remain comparable.

API key resolution: each client either takes ``api_key=...`` explicitly or
reads its provider-conventional environment variable (e.g.
``ANTHROPIC_API_KEY``, ``OPENAI_API_KEY``, ``GOOGLE_API_KEY``).

The shared request/parse flow lives in :class:`BaseCloudClient`; each
concrete client only supplies the provider-specific pieces (auth, endpoint
URL, request payload shape, and response-text extraction).
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from typing import Any, Protocol

import requests

from pyimgtag._http_retry import MAX_ATTEMPTS as _MAX_ATTEMPTS
from pyimgtag._http_retry import RETRY_STATUSES as _RETRY_STATUSES
from pyimgtag._http_retry import post_with_retry
from pyimgtag.models import JudgeScores, TagResult
from pyimgtag.ollama_client import (
    _JUDGE_PROMPT,
    _PROMPT_BASE,
    JUDGE_IMAGE_UNREADABLE,
    JUDGE_UNPARSEABLE,
    JudgeErrorMixin,
    _build_prompt_with_context,
    _parse_judge_response,
    _parse_response,
    prepare_image_b64,
    short_request_error,
)


class ImageClient(Protocol):
    """Structural type implemented by every vision-model client.

    Both the local :class:`pyimgtag.ollama_client.OllamaClient` and the cloud
    clients in this module satisfy it, so callers (and mypy) can treat the value
    returned by :func:`make_image_client` uniformly without importing each
    concrete class.
    """

    def tag_image(self, file_path: str, context: dict | None = None) -> TagResult:
        """Tag an image and return a :class:`TagResult`."""

    def judge_image(self, file_path: str) -> JudgeScores | None:
        """Score an image and return :class:`JudgeScores`, or None on failure."""

    def close(self) -> None:
        """Release any underlying resources (e.g. an HTTP session)."""


# Sensible defaults for each provider. Users can override with --model.
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_GEMINI_MODEL = "gemini-1.5-flash"

DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com"
DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com"

_ANTHROPIC_API_VERSION = "2023-06-01"

# Token budget for the JSON response; matches Ollama's _MODEL_MAX_TOKENS.
_CLOUD_MAX_TOKENS = 1024

logger = logging.getLogger(__name__)

# --- throttling -----------------------------------------------------------
# 429/503 retry with backoff lives in pyimgtag._http_retry, shared with
# OllamaClient; the names are re-imported here for existing callers and tests.


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


class BaseCloudClient(JudgeErrorMixin, ABC):
    """Shared tag/judge flow for the cloud vision clients.

    Concrete subclasses provide only the provider-specific hooks: API-key
    resolution, session headers, the endpoint URL, the request payload
    shape, and how to pull the model's text out of the JSON response.
    """

    #: Backend name used in error messages (e.g. ``"anthropic"``).
    _backend: str

    #: Optional :class:`pyimgtag.prompt_template.PromptBuilder`; see
    #: :attr:`pyimgtag.ollama_client.OllamaClient.prompt_builder`.
    prompt_builder: Any = None

    def __init__(
        self,
        model: str,
        max_dim: int,
        timeout: int,
        api_key: str | None,
        base_url: str,
    ) -> None:
        self.model = model
        self.max_dim = max_dim
        self.timeout = timeout
        self.base_url = base_url.rstrip("/")
        self._api_key = self._resolve_api_key(api_key)
        self._session = requests.Session()
        self._session.headers.update(self._session_headers())

    @abstractmethod
    def _resolve_api_key(self, explicit: str | None) -> str:
        """Return the API key, from *explicit* or the provider's env var(s)."""

    @abstractmethod
    def _session_headers(self) -> dict[str, str]:
        """Return the auth/content-type headers for every request."""

    @abstractmethod
    def _request_url(self) -> str:
        """Return the full endpoint URL to POST to."""

    @abstractmethod
    def _build_payload(self, prompt: str, img_b64: str) -> dict[str, Any]:
        """Return the provider-shaped JSON request body for *prompt* + image."""

    @abstractmethod
    def _extract_text(self, data: Any) -> str | None:
        """Pull the model's text out of the decoded JSON response *data*.

        Raises ``KeyError``/``IndexError``/``TypeError`` when the response
        shape is unexpected; may return ``None`` when the provider sent a
        literal null text.
        """

    def tag_image(self, file_path: str, context: dict | None = None) -> TagResult:
        """Tag an image via the provider vision API.

        Args:
            file_path: Path to the image file.
            context: Optional EXIF/geocoding hints (``date``, ``city``,
                ``region``, ``country``, ``lat``, ``lon``) used to enrich the
                prompt.

        Returns:
            A :class:`TagResult`. Failures (image load, request, empty or
            malformed response) are reported in ``TagResult.error`` rather than
            raised.
        """
        try:
            img_b64 = prepare_image_b64(file_path, self.max_dim)
        except (OSError, ValueError, RuntimeError) as e:
            return TagResult(error=f"Image load failed: {e}")
        if self.prompt_builder is not None:
            prompt = self.prompt_builder.render(context)
        else:
            prompt = _build_prompt_with_context(context) if context else _PROMPT_BASE
        text = self._call(prompt, img_b64, on_error_msg=f"{self._backend} request failed")
        if isinstance(text, TagResult):
            return text
        if text is None:
            return TagResult(error="empty response")
        return _parse_response(text)

    def judge_image(self, file_path: str) -> JudgeScores | None:
        """Score an image with the photo-judge rubric.

        Returns None on any failure (image load, request, or parse) — unlike
        :meth:`tag_image`, which reports failures via ``TagResult.error``.
        :attr:`last_error` then says why, and is cleared by the next success.
        """
        try:
            img_b64 = prepare_image_b64(file_path, self.max_dim)
        except (OSError, ValueError, RuntimeError):
            self._judge_failed(JUDGE_IMAGE_UNREADABLE)
            return None
        self._judge_ok()  # a reason left by an earlier call must not leak into this one
        text = self._call(_JUDGE_PROMPT, img_b64, on_error_msg=None)
        if not isinstance(text, str):
            # _call records request and response-shape failures; a reply with
            # no text at all (e.g. OpenAI ``content: null`` on a refusal or a
            # content filter) reaches here with nothing recorded.
            if self.last_error is None:
                self._judge_failed(JUDGE_UNPARSEABLE)
            return None
        scores = _parse_judge_response(text)
        if scores is None:
            self._judge_failed(JUDGE_UNPARSEABLE)
            return None
        self._judge_ok()
        return scores

    def _call(
        self,
        prompt: str,
        img_b64: str,
        *,
        on_error_msg: str | None,
    ) -> str | TagResult | None:
        """POST the prompt and image, and return the model's text response.

        Tri-modal return contract used by :meth:`tag_image` / :meth:`judge_image`:

        - ``str``: the model text, on success.
        - :class:`TagResult` with ``error`` set: on HTTP or response-shape
          failure when ``on_error_msg`` is provided (the ``tag_image`` path).
        - ``None``: on the same failures when ``on_error_msg`` is ``None`` (the
          ``judge_image`` path, which swallows failures into a ``None`` score).

        Throttling: every attempt first acquires from the process-wide
        ``--max-rps`` bucket (a no-op when unset). HTTP 429/503 are retried up
        to :data:`_MAX_ATTEMPTS` times honouring ``Retry-After``, then fall
        back to capped exponential backoff with jitter. A sustained rate limit
        therefore fails the image with a clear message in bounded time instead
        of hanging.
        """
        payload = self._build_payload(prompt, img_b64)
        url = self._request_url()
        try:
            resp = post_with_retry(
                self._session, url, json=payload, timeout=self.timeout, label=self._backend
            )
        except requests.RequestException as e:
            if on_error_msg:
                return TagResult(error=f"{on_error_msg}: {e}")
            self._judge_failed(short_request_error(e))
            return None

        status = getattr(resp, "status_code", None)
        if status in _RETRY_STATUSES:
            detail = f"{self._backend} rate limited (HTTP {status}) after {_MAX_ATTEMPTS} attempts"
            if on_error_msg:
                return TagResult(error=detail)
            self._judge_failed(
                f"request failed: rate limited (HTTP {status}) after {_MAX_ATTEMPTS} attempts"
            )
            return None

        try:
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            if on_error_msg:
                return TagResult(error=f"{on_error_msg}: {e}")
            # requests' JSONDecodeError is both a RequestException and a
            # ValueError: a body that is not JSON is a parse failure.
            self._judge_failed(
                JUDGE_UNPARSEABLE if isinstance(e, ValueError) else short_request_error(e)
            )
            return None
        try:
            return self._extract_text(data)
        except (KeyError, IndexError, TypeError):
            detail = f"{self._backend} response shape unexpected: {str(data)[:160]!r}"
            if on_error_msg:
                return TagResult(error=detail)
            self._judge_failed(JUDGE_UNPARSEABLE)
            return None

    def close(self) -> None:
        """Release the underlying HTTP session."""
        self._session.close()


class AnthropicClient(BaseCloudClient):
    """Vision client for Anthropic's Claude API (``/v1/messages``)."""

    _backend = "anthropic"

    def __init__(
        self,
        model: str = DEFAULT_ANTHROPIC_MODEL,
        max_dim: int = 1280,
        timeout: int = 120,
        api_key: str | None = None,
        base_url: str = DEFAULT_ANTHROPIC_BASE_URL,
    ) -> None:
        super().__init__(
            model=model, max_dim=max_dim, timeout=timeout, api_key=api_key, base_url=base_url
        )

    def _resolve_api_key(self, explicit: str | None) -> str:
        return _require_api_key(explicit, "ANTHROPIC_API_KEY", "anthropic")

    def _session_headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key,
            "anthropic-version": _ANTHROPIC_API_VERSION,
            "content-type": "application/json",
        }

    def _request_url(self) -> str:
        return f"{self.base_url}/v1/messages"

    def _build_payload(self, prompt: str, img_b64: str) -> dict[str, Any]:
        return {
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

    def _extract_text(self, data: Any) -> str | None:
        return data["content"][0]["text"]


class OpenAIClient(BaseCloudClient):
    """Vision client for OpenAI's chat completions API."""

    _backend = "openai"

    def __init__(
        self,
        model: str = DEFAULT_OPENAI_MODEL,
        max_dim: int = 1280,
        timeout: int = 120,
        api_key: str | None = None,
        base_url: str = DEFAULT_OPENAI_BASE_URL,
    ) -> None:
        super().__init__(
            model=model, max_dim=max_dim, timeout=timeout, api_key=api_key, base_url=base_url
        )

    def _resolve_api_key(self, explicit: str | None) -> str:
        return _require_api_key(explicit, "OPENAI_API_KEY", "openai")

    def _session_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _request_url(self) -> str:
        return f"{self.base_url}/v1/chat/completions"

    def _build_payload(self, prompt: str, img_b64: str) -> dict[str, Any]:
        return {
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

    def _extract_text(self, data: Any) -> str | None:
        return data["choices"][0]["message"]["content"]


class GeminiClient(BaseCloudClient):
    """Vision client for Google's Gemini API (``generateContent``)."""

    _backend = "gemini"

    def __init__(
        self,
        model: str = DEFAULT_GEMINI_MODEL,
        max_dim: int = 1280,
        timeout: int = 120,
        api_key: str | None = None,
        base_url: str = DEFAULT_GEMINI_BASE_URL,
    ) -> None:
        super().__init__(
            model=model, max_dim=max_dim, timeout=timeout, api_key=api_key, base_url=base_url
        )

    def _resolve_api_key(self, explicit: str | None) -> str:
        return _resolve_gemini_key(explicit)

    def _session_headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json", "x-goog-api-key": self._api_key}

    def _request_url(self) -> str:
        return f"{self.base_url}/v1beta/models/{self.model}:generateContent"

    def _build_payload(self, prompt: str, img_b64: str) -> dict[str, Any]:
        return {
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

    def _extract_text(self, data: Any) -> str | None:
        return data["candidates"][0]["content"]["parts"][0]["text"]


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
    prompt_builder: Any = None,
    ollama_class: Any = None,
) -> ImageClient:
    """Build a vision-model client for *backend*.

    Args:
        backend: One of ``"ollama"``, ``"anthropic"``, ``"openai"``, ``"gemini"``.
        model: Model name. ``None`` falls back to the per-backend default.
        max_dim: Max image dimension before sending.
        timeout: HTTP timeout in seconds.
        api_key: Explicit API key (cloud backends only). ``None`` reads the
            provider-conventional environment variable.
        api_base: Override the base URL. For ``ollama`` this is the full
            Ollama URL (default :data:`DEFAULT_OLLAMA_URL`); for cloud
            backends it points at the API endpoint root.
        prompt_builder: Optional :class:`pyimgtag.prompt_template.PromptBuilder`
            that renders the tagging prompt (controlled vocabulary, custom
            template, output language). ``None`` keeps the built-in prompt.
        ollama_class: Class to build for ``ollama``; defaults to
            :class:`~pyimgtag.ollama_client.OllamaClient`. The commands pass
            the name they import, so tests that patch
            ``pyimgtag.commands.<cmd>.OllamaClient`` still take effect.

    Returns:
        An :class:`ImageClient` exposing ``tag_image``, ``judge_image``, and
        ``close``.

    Raises:
        ValueError: If *backend* is unrecognised.
        CloudClientError: If a cloud backend is selected without an API key.
    """
    backend_norm = backend.lower().strip()
    client: Any
    if backend_norm == "ollama":
        from pyimgtag.ollama_client import DEFAULT_OLLAMA_MODEL, DEFAULT_OLLAMA_URL, OllamaClient

        client = (ollama_class or OllamaClient)(
            model=model or DEFAULT_OLLAMA_MODEL,
            # Only a missing URL falls back; an explicit one (even "") is
            # passed through, as the commands always did.
            base_url=DEFAULT_OLLAMA_URL if api_base is None else api_base,
            max_dim=max_dim,
            timeout=timeout,
        )
    elif backend_norm == "anthropic":
        client = AnthropicClient(
            model=model or DEFAULT_ANTHROPIC_MODEL,
            max_dim=max_dim,
            timeout=timeout,
            api_key=api_key,
            base_url=api_base or DEFAULT_ANTHROPIC_BASE_URL,
        )
    elif backend_norm == "openai":
        client = OpenAIClient(
            model=model or DEFAULT_OPENAI_MODEL,
            max_dim=max_dim,
            timeout=timeout,
            api_key=api_key,
            base_url=api_base or DEFAULT_OPENAI_BASE_URL,
        )
    elif backend_norm == "gemini":
        client = GeminiClient(
            model=model or DEFAULT_GEMINI_MODEL,
            max_dim=max_dim,
            timeout=timeout,
            api_key=api_key,
            base_url=api_base or DEFAULT_GEMINI_BASE_URL,
        )
    else:
        raise ValueError(
            f"Unknown backend {backend!r}; expected one of ollama, anthropic, openai, gemini"
        )
    if prompt_builder is not None:
        client.prompt_builder = prompt_builder
    return client


SUPPORTED_BACKENDS: tuple[str, ...] = ("ollama", "anthropic", "openai", "gemini")
