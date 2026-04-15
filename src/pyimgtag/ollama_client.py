"""Ollama vision model client for image tagging.

Makes exactly one API call per image with a compact prompt to minimise token
usage.  Images are resized and JPEG-compressed before encoding.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re

import requests
from PIL import Image

from pyimgtag.heic_converter import convert_heic_to_jpeg, is_heic, sips_available
from pyimgtag.models import TagResult

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:
    pass

_PROMPT_BASE = (
    "Return compact JSON only. "
    "Identify 1 to 5 major visible tags for this image. "
    "Use short lowercase nouns or noun phrases. "
    "Do not guess people names. "
    "Do not infer exact city from image content. "
    "Rules for extra fields:\n"
    "- scene_category: one of indoor_home, indoor_work, outdoor_leisure, outdoor_travel, "
    "transport, other\n"
    "- emotional_tone: one of positive, neutral, negative, mixed\n"
    "- cleanup_class: keep (clear value), review (uncertain), "
    "delete (blurry/duplicate/screenshot junk)\n"
    "- has_text: true if image contains readable text, else false\n"
    "- text_summary: brief summary of readable text if has_text is true, else omit\n"
    "- event_hint: one of outing, gathering, work, travel, daily, other\n"
    "- significance: one of high, medium, low\n"
    'Schema: {"tags":["..."], "summary":"...", "scene_category":"...", '
    '"emotional_tone":"...", "cleanup_class":"...", "has_text":false, '
    '"text_summary":"...", "event_hint":"...", "significance":"..."}'
)


def _build_prompt_with_context(context: dict) -> str:
    """Build a context-enriched prompt from EXIF/geocoding data."""
    ctx_lines = []
    if context.get("date"):
        ctx_lines.append(f"- Date: {context['date']}")
    loc_parts = [
        p for p in [context.get("city"), context.get("region"), context.get("country")] if p
    ]
    if loc_parts:
        ctx_lines.append(f"- Location: {', '.join(loc_parts)}")
    if context.get("lat") is not None and context.get("lon") is not None:
        ctx_lines.append(f"- GPS: {context['lat']}, {context['lon']}")
    if not ctx_lines:
        return _PROMPT_BASE
    ctx_block = "\n".join(ctx_lines)
    return (
        "Return compact JSON only.\n"
        "Tag this image for a photo gallery.\n\n"
        "Context (use to improve tag relevance, not as tags themselves):\n"
        f"{ctx_block}\n\n"
        "Rules:\n"
        "- 1 to 5 tags maximum\n"
        "- short lowercase noun phrases\n"
        "- prefer broad useful tags over overly specific ones\n"
        "- ignore small background objects\n"
        "- no names\n"
        "- no place guesses from image content "
        "(location context above is from GPS metadata)\n"
        "- no explanation\n\n"
        "Rules for extra fields:\n"
        "- scene_category: one of indoor_home, indoor_work, outdoor_leisure, outdoor_travel, "
        "transport, other\n"
        "- emotional_tone: one of positive, neutral, negative, mixed\n"
        "- cleanup_class: keep (clear value), review (uncertain), "
        "delete (blurry/duplicate/screenshot junk)\n"
        "- has_text: true if image contains readable text, else false\n"
        "- text_summary: brief summary of readable text if has_text is true, else omit\n"
        "- event_hint: one of outing, gathering, work, travel, daily, other\n"
        "- significance: one of high, medium, low\n\n"
        'Schema: {"tags":["..."], "summary":"...", "scene_category":"...", '
        '"emotional_tone":"...", "cleanup_class":"...", "has_text":false, '
        '"text_summary":"...", "event_hint":"...", "significance":"..."}'
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

    def tag_image(self, file_path: str, context: dict | None = None) -> TagResult:
        """Tag an image using the vision model.  One call per image."""
        try:
            img_b64 = self._prepare_image(file_path)
        except Exception as e:
            return TagResult(error=f"Image load failed: {e}")

        prompt = _build_prompt_with_context(context) if context else _PROMPT_BASE
        try:
            resp = self._session.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "user", "content": prompt, "images": [img_b64]},
                    ],
                    "stream": False,
                    "think": False,
                    "options": {"temperature": 0.1, "num_predict": 512},
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            return TagResult(error=f"Ollama request failed: {e}")

        try:
            text = resp.json().get("message", {}).get("content", "")
            parsed = _parse_response(text)
        except Exception as e:
            return TagResult(error=f"Response parse failed: {e}")
        return parsed

    def _prepare_image(self, file_path: str) -> str:
        """Load, resize to *max_dim*, convert to JPEG, and base64-encode."""
        temp_jpeg: str | None = None
        open_path = file_path

        if is_heic(file_path) and sips_available():
            temp_jpeg_path = convert_heic_to_jpeg(file_path)
            temp_jpeg = str(temp_jpeg_path)
            open_path = temp_jpeg

        try:
            with Image.open(open_path) as raw:
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
        finally:
            if temp_jpeg is not None:
                try:
                    os.unlink(temp_jpeg)
                    temp_dir = os.path.dirname(temp_jpeg)
                    if temp_dir and not os.listdir(temp_dir):
                        os.rmdir(temp_dir)
                except OSError:
                    pass

    def close(self) -> None:
        self._session.close()


_SCENE_CATEGORY_ALLOWED = frozenset(
    {"indoor_home", "indoor_work", "outdoor_leisure", "outdoor_travel", "transport", "other"}
)
_EMOTIONAL_TONE_ALLOWED = frozenset({"positive", "neutral", "negative", "mixed"})
_CLEANUP_CLASS_ALLOWED = frozenset({"keep", "review", "delete"})
_EVENT_HINT_ALLOWED = frozenset({"outing", "gathering", "work", "travel", "daily", "other"})
_SIGNIFICANCE_ALLOWED = frozenset({"high", "medium", "low"})


def _validated_enum(value: object, allowed: frozenset[str]) -> str | None:
    """Return value if it is a string in allowed, else None."""
    if isinstance(value, str) and value in allowed:
        return value
    return None


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

    scene_category = _validated_enum(parsed.get("scene_category"), _SCENE_CATEGORY_ALLOWED)
    emotional_tone = _validated_enum(parsed.get("emotional_tone"), _EMOTIONAL_TONE_ALLOWED)
    cleanup_class = _validated_enum(parsed.get("cleanup_class"), _CLEANUP_CLASS_ALLOWED)

    has_text_raw = parsed.get("has_text", False)
    has_text = bool(has_text_raw) if isinstance(has_text_raw, bool) else False

    text_summary = parsed.get("text_summary")
    if text_summary and not isinstance(text_summary, str):
        text_summary = str(text_summary)
    if not has_text:
        text_summary = None

    event_hint = _validated_enum(parsed.get("event_hint"), _EVENT_HINT_ALLOWED)
    significance = _validated_enum(parsed.get("significance"), _SIGNIFICANCE_ALLOWED)

    return TagResult(
        tags=tags,
        summary=summary,
        raw_response=raw,
        scene_category=scene_category,
        emotional_tone=emotional_tone,
        cleanup_class=cleanup_class,
        has_text=has_text,
        text_summary=text_summary,
        event_hint=event_hint,
        significance=significance,
    )


def _try_json(text: str) -> dict | None:
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None
