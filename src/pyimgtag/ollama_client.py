"""Ollama vision model client for image tagging.

Makes exactly one API call per image with a compact prompt to minimise token
usage.  Images are resized and JPEG-compressed before encoding.
"""

from __future__ import annotations

import base64
import io
import json
import re

import requests
from PIL import Image

from pyimgtag.models import TagResult

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:
    pass

_PROMPT = (
    "Return compact JSON only. "
    "Identify 1 to 5 major visible tags for this image. "
    "Use short lowercase nouns or noun phrases. "
    "Do not guess people names. "
    "Do not infer exact city from image content. "
    'Schema: {"tags":["..."], "summary":"..."}'
)


class OllamaClient:
    """Client for local Ollama vision model."""

    def __init__(
        self,
        model: str = "gemma4:e4b",
        base_url: str = "http://localhost:11434",
        max_dim: int = 1280,
        timeout: int = 120,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.max_dim = max_dim
        self.timeout = timeout
        self._session = requests.Session()

    def tag_image(self, file_path: str) -> TagResult:
        """Tag an image using the vision model.  One call per image."""
        try:
            img_b64 = self._prepare_image(file_path)
        except Exception as e:
            return TagResult(error=f"Image load failed: {e}")

        try:
            resp = self._session.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "user", "content": _PROMPT, "images": [img_b64]},
                    ],
                    "stream": False,
                    "think": False,
                    "options": {"temperature": 0.1, "num_predict": 256},
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            return TagResult(error=f"Ollama request failed: {e}")

        try:
            text = resp.json().get("message", {}).get("content", "")
            return _parse_response(text)
        except Exception as e:
            return TagResult(error=f"Response parse failed: {e}")

    def _prepare_image(self, file_path: str) -> str:
        """Load, resize to *max_dim*, convert to JPEG, and base64-encode."""
        with Image.open(file_path) as raw:
            converted = raw.convert("RGB")
            w, h = converted.size
            if max(w, h) > self.max_dim:
                ratio = self.max_dim / max(w, h)
                converted = converted.resize(
                    (int(w * ratio), int(h * ratio)), Image.Resampling.LANCZOS
                )
            buf = io.BytesIO()
            converted.save(buf, format="JPEG", quality=85)
            return base64.b64encode(buf.getvalue()).decode("ascii")

    def close(self) -> None:
        self._session.close()


# ---------------------------------------------------------------------------
# response parsing
# ---------------------------------------------------------------------------


def _parse_response(text: str) -> TagResult:
    raw = text.strip()
    parsed = _try_json(raw)
    if parsed is None:
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
        if m:
            parsed = _try_json(m.group(1))
    if parsed is None:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            parsed = _try_json(m.group(0))
    if parsed is None:
        return TagResult(raw_response=raw, error="Could not parse JSON from model response")

    tags = parsed.get("tags", [])
    if not isinstance(tags, list):
        tags = []
    tags = [str(t).lower().strip() for t in tags if t][:5]
    summary = parsed.get("summary")
    if summary and not isinstance(summary, str):
        summary = str(summary)
    return TagResult(tags=tags, summary=summary, raw_response=raw)


def _try_json(text: str) -> dict | None:
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None
