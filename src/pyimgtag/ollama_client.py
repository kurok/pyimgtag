"""Ollama vision model client for image tagging.

Makes exactly one API call per image with a compact prompt to minimise token
usage.  Images are resized and JPEG-compressed before encoding.
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import re

import requests
from PIL import Image

from pyimgtag.heic_converter import convert_heic_to_jpeg, is_heic, sips_available
from pyimgtag.models import JudgeScores, TagResult, normalize_tags
from pyimgtag.raw_converter import (
    convert_raw_with_rawpy,
    extract_raw_thumbnail,
    is_raw,
    rawpy_available,
)

with contextlib.suppress(ImportError):
    import pillow_heif

    pillow_heif.register_heif_opener()

_MODEL_TEMPERATURE: float = 0.3
_MODEL_MAX_TOKENS: int = 512


_PROMPT_FIELDS = """\
Reply with ONLY a valid JSON object — no markdown, no explanation. Required fields:
- tags: list of 1-5 short lowercase noun phrases (no names, no location guesses from image)
- summary: one sentence describing the image
- scene_category: indoor_home | indoor_work | outdoor_leisure | outdoor_travel | transport | other
- emotional_tone: one of positive | neutral | negative | mixed
- cleanup_class: keep (clear value) | review (uncertain) | delete (blurry/duplicate/junk)
- has_text: true or false
- text_summary: visible text description if has_text is true, otherwise null
- event_hint: one of outing | gathering | work | travel | daily | other
- significance: one of high | medium | low"""

_PROMPT_BASE = "Tag this image for a photo gallery.\n\n" + _PROMPT_FIELDS

_JUDGE_PROMPT = """\
You are a professional photo judge. Score this photograph on each criterion \
as a whole integer from 1 to 10, where 1=poor, 5=acceptable/competent, \
8=strong, 10=exceptional. Use whole numbers only — no decimals.

Respond with ONLY a valid JSON object. Required fields:
- impact: 1-10  (emotional pull, memorability)
- story_subject: 1-10  (clear subject and meaning)
- composition_center: 1-10  (visual flow, balance, center of interest)
- lighting: 1-10  (quality, control, mood support)
- creativity_style: 1-10  (originality of treatment)
- color_mood: 1-10  (color balance and mood fit)
- presentation_crop: 1-10  (crop, framing, aspect ratio)
- technical_excellence: 1-10  (exposure, retouching, overall finish)
- focus_sharpness: 1-10  (critical detail is sharp; blur is intentional)
- exposure_tonal: 1-10  (highlights and shadows under control)
- noise_cleanliness: 1-10  (clean detail, no distracting grain)
- subject_separation: 1-10  (subject stands out from background)
- edit_integrity: 1-10  (no halos, overprocessing, or clone artefacts)
- verdict: one sentence naming the key strength and key weakness

Score honestly. A 5 means competent and deliverable. A 10 means exceptional. \
Output integers only — no fractional values like 7.5."""

_JUDGE_SCORE_FIELDS: tuple[str, ...] = (
    "impact",
    "story_subject",
    "composition_center",
    "lighting",
    "creativity_style",
    "color_mood",
    "presentation_crop",
    "technical_excellence",
    "focus_sharpness",
    "exposure_tonal",
    "noise_cleanliness",
    "subject_separation",
    "edit_integrity",
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
        "Tag this image for a photo gallery.\n\n"
        "Context (use to improve tag relevance, not as tags themselves):\n"
        f"{ctx_block}\n\n"
        "Prefer broad useful tags. Ignore small background objects. "
        "No place guesses from image content "
        "(location context above is from GPS metadata).\n\n" + _PROMPT_FIELDS
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
        except (OSError, ValueError, RuntimeError) as e:
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
                    "format": "json",
                    "stream": False,
                    "think": False,
                    "options": {
                        "temperature": _MODEL_TEMPERATURE,
                        "num_predict": _MODEL_MAX_TOKENS,
                    },
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            return TagResult(error=f"Ollama request failed: {e}")

        try:
            text = resp.json().get("message", {}).get("content", "")
            parsed = _parse_response(text)
        except (KeyError, ValueError, AttributeError) as e:
            return TagResult(error=f"Response parse failed: {e}")
        return parsed

    def judge_image(self, file_path: str) -> JudgeScores | None:
        """Score an image with the photo-judge rubric. Returns None on failure."""
        try:
            img_b64 = self._prepare_image(file_path)
        except (OSError, ValueError, RuntimeError):
            return None

        try:
            resp = self._session.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "user", "content": _JUDGE_PROMPT, "images": [img_b64]},
                    ],
                    "format": "json",
                    "stream": False,
                    "think": False,
                    "options": {
                        "temperature": _MODEL_TEMPERATURE,
                        "num_predict": _MODEL_MAX_TOKENS,
                    },
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
        except requests.RequestException:
            return None

        try:
            text = resp.json().get("message", {}).get("content", "")
            return _parse_judge_response(text)
        except (KeyError, ValueError, AttributeError):
            return None

    def _prepare_image(self, file_path: str) -> str:
        """Backwards-compatible wrapper around :func:`prepare_image_b64`."""
        return prepare_image_b64(file_path, self.max_dim)

    def close(self) -> None:
        self._session.close()


def prepare_image_b64(file_path: str, max_dim: int) -> str:
    """Load *file_path*, resize to ``max_dim`` longest edge, encode as JPEG b64.

    Handles HEIC and RAW inputs by routing through ``sips`` / exiftool / rawpy
    first and cleaning up the intermediate JPEG. The same helper backs every
    image-model client (Ollama, Anthropic, OpenAI, Gemini) so they all see the
    exact same bytes for a given input.
    """
    temp_jpeg: str | None = None
    open_path = file_path

    if is_heic(file_path) and sips_available():
        temp_jpeg_path = convert_heic_to_jpeg(file_path)
        temp_jpeg = str(temp_jpeg_path)
        open_path = temp_jpeg
    elif is_raw(file_path):
        try:
            temp_jpeg_path = extract_raw_thumbnail(file_path)
        except RuntimeError:
            if rawpy_available():
                temp_jpeg_path = convert_raw_with_rawpy(file_path)
            else:
                raise
        temp_jpeg = str(temp_jpeg_path)
        open_path = temp_jpeg

    try:
        with Image.open(open_path) as raw:
            converted = raw.convert("RGB")
            w, h = converted.size
            if max(w, h) > max_dim:
                ratio = max_dim / max(w, h)
                converted = converted.resize(
                    (int(w * ratio), int(h * ratio)), Image.Resampling.LANCZOS
                )
            buf = io.BytesIO()
            converted.save(buf, format="JPEG", quality=85)
            return base64.b64encode(buf.getvalue()).decode("ascii")
    finally:
        if temp_jpeg is not None:
            with contextlib.suppress(OSError):
                os.unlink(temp_jpeg)
                temp_dir = os.path.dirname(temp_jpeg)
                if temp_dir and not os.listdir(temp_dir):
                    os.rmdir(temp_dir)


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
        parsed = _extract_first_json_object(raw)
    if parsed is None:
        return TagResult(raw_response=raw, error="Could not parse JSON from model response")

    raw_tags = parsed.get("tags", [])
    if not isinstance(raw_tags, list):
        raw_tags = []
    tags = normalize_tags(raw_tags)

    summary = parsed.get("summary")
    if summary and not isinstance(summary, str):
        summary = str(summary)

    scene_category = _validated_enum(parsed.get("scene_category"), _SCENE_CATEGORY_ALLOWED)
    emotional_tone = _validated_enum(parsed.get("emotional_tone"), _EMOTIONAL_TONE_ALLOWED)
    cleanup_class = _validated_enum(parsed.get("cleanup_class"), _CLEANUP_CLASS_ALLOWED)

    has_text_raw = parsed.get("has_text", False)
    has_text = bool(has_text_raw) if isinstance(has_text_raw, (bool, int)) else False

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


def _parse_judge_response(text: str) -> JudgeScores | None:
    raw = text.strip()
    parsed = _try_json(raw)
    if parsed is None:
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
        if m:
            parsed = _try_json(m.group(1))
    if parsed is None:
        parsed = _extract_first_json_object(raw)
    if parsed is None:
        return None

    def _score(key: str) -> int:
        val = parsed.get(key, 5)
        try:
            return int(round(max(1.0, min(10.0, float(val)))))
        except (TypeError, ValueError):
            return 5

    verdict = parsed.get("verdict", "")
    if not isinstance(verdict, str):
        verdict = ""

    return JudgeScores(
        **{k: _score(k) for k in _JUDGE_SCORE_FIELDS},
        verdict=verdict,
    )


def _try_json(text: str) -> dict | None:
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


def _extract_first_json_object(text: str) -> dict | None:
    """Scan text character-by-character and return the first valid JSON object found.

    Handles model responses that include {word} placeholders, thinking tokens, or other
    prose before the actual JSON — cases where a greedy regex would capture too much.
    """
    decoder = json.JSONDecoder()
    i = 0
    while i < len(text):
        if text[i] == "{":
            try:
                obj, _ = decoder.raw_decode(text, i)
                if isinstance(obj, dict):
                    return obj
            except (json.JSONDecodeError, ValueError):
                pass  # not valid JSON at this position; try the next {
        i += 1
    return None
