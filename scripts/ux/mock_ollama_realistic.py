#!/usr/bin/env python3
"""A mock Ollama that *looks at the image* instead of replaying a fixed list.

The real thing analyses pixels; this mock decodes the base64 image from the
request, works out its dominant colour, brightness and contrast, and answers
with tags, a scene category, a cleanup class and a quality score that follow
from what it "sees". That makes a generated corpus behave like a real library:
the dark night shots come back as cleanup candidates, the screenshots come
back with text, the burst frames get similar tags, and `judge` ranks the
photos by something other than a constant.

Usage:  python mock_ollama_realistic.py [PORT]
"""

from __future__ import annotations

import base64
import contextlib
import http.server
import io
import json
import statistics
import sys
from typing import Any

from PIL import Image

DEFAULT_PORT = 11435


def _features(b64: str) -> dict[str, float] | None:
    """Average colour / brightness / saturation / contrast of the image."""
    try:
        raw = base64.b64decode(b64)
        with Image.open(io.BytesIO(raw)) as im:
            small = im.convert("RGB").resize((48, 48))
            pixels = list(small.getdata())
    except Exception:
        return None
    n = len(pixels)
    r = sum(p[0] for p in pixels) / n
    g = sum(p[1] for p in pixels) / n
    b = sum(p[2] for p in pixels) / n
    brightness = (r + g + b) / 3
    mx, mn = max(r, g, b), min(r, g, b)
    saturation = 0 if mx == 0 else (mx - mn) / mx
    lum = [0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2] for p in pixels]
    contrast = statistics.pstdev(lum) if len(lum) > 1 else 0.0
    return {"r": r, "g": g, "b": b, "bright": brightness, "sat": saturation, "contrast": contrast}


def _tags(f: dict[str, float]) -> tuple[list[str], str, str, bool, str, str]:
    """Tags, scene category, cleanup class, has_text, tone, significance."""
    r, g, b = f["r"], f["g"], f["b"]
    bright, sat, contrast = f["bright"], f["sat"], f["contrast"]
    warm = r > b + 12
    green = g > r + 6 and g > b + 6
    blue = b > r + 12

    tags: list[str] = []
    if bright < 70:
        tags += ["night", "low-light"]
        scene = "outdoor_leisure"
        tone = "quiet"
    elif sat < 0.08 and bright > 150:
        tags += ["screenshot", "document"]
        scene = "indoor_work"
        tone = "neutral"
    elif warm and bright > 120:
        tags += ["sunset", "golden-hour", "warm light"]
        scene = "outdoor_leisure"
        tone = "positive"
    elif green:
        tags += ["trees", "greenery", "outdoors"]
        scene = "outdoor_leisure"
        tone = "calm"
    elif blue and bright > 130:
        tags += ["sky", "water", "beach"]
        scene = "outdoor_travel"
        tone = "calm"
    elif sat < 0.12:
        tags += ["document", "wall"]
        scene = "indoor_home"
        tone = "neutral"
    else:
        tags += ["street", "architecture"]
        scene = "outdoor_travel"
        tone = "neutral"

    if contrast < 12:
        tags += ["flat", "washed-out"]
    if bright < 55:
        cleanup = "delete"
    elif contrast < 14 or sat < 0.06:
        cleanup = "review"
    else:
        cleanup = "keep"

    has_text = bool(sat < 0.08 and bright > 150)
    significance = (
        "high" if (contrast > 40 and bright > 110) else ("low" if bright < 70 else "medium")
    )
    return tags[:5], scene, cleanup, has_text, tone, significance


def _tag_reply(b64: str) -> dict[str, Any]:
    f = _features(b64)
    if f is None:
        return {
            "tags": ["unreadable"],
            "summary": "the file could not be read as an image",
            "scene_category": "other",
            "emotional_tone": "neutral",
            "cleanup_class": "review",
            "has_text": False,
            "text_summary": None,
            "event_hint": "daily",
            "significance": "low",
        }
    tags, scene, cleanup, has_text, tone, significance = _tags(f)
    return {
        "tags": tags,
        "summary": f"{', '.join(tags[:2])} photo",
        "scene_category": scene,
        "emotional_tone": tone,
        "cleanup_class": cleanup,
        "has_text": has_text,
        "text_summary": "screen text" if has_text else None,
        "event_hint": "outing" if scene in ("outdoor_leisure", "outdoor_travel") else "daily",
        "significance": significance,
    }


def _judge_reply(b64: str) -> dict[str, Any]:
    f = _features(b64)
    if f is None:
        return {"score": 1, "reason": "the file could not be read as an image"}
    bright, contrast, sat = f["bright"], f["contrast"], f["sat"]
    score = 5
    score += 2 if contrast > 45 else (1 if contrast > 25 else -1)
    score += 1 if 90 < bright < 200 else -1
    score += 1 if sat > 0.25 else 0
    score -= 2 if bright < 55 else 0
    score = max(1, min(10, score))
    if score >= 8:
        reason = "well exposed, good separation and a clear subject"
    elif score >= 6:
        reason = "usable frame, but the light is flat and the composition is ordinary"
    elif score >= 4:
        reason = "low contrast and no clear subject; usable only as a record"
    else:
        reason = "very dark and blurred; nothing readable in the frame"
    return {"score": score, "reason": reason}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == "/api/tags":
            body = json.dumps(
                {
                    "models": [
                        {"name": "gemma4:e4b", "model": "gemma4:e4b", "size": 0, "digest": "mock"}
                    ]
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            payload = {}
        messages = payload.get("messages") or [{}]
        content = messages[0].get("content", "") if messages else ""
        images = messages[0].get("images") or [] if messages else []
        b64 = images[0] if images else ""

        if "professional photography judge" in content:
            reply = _judge_reply(b64)
        else:
            reply = _tag_reply(b64)

        body = json.dumps({"message": {"content": json.dumps(reply)}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        pass


def main(port: int | None = None) -> None:
    if port is None:
        port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    print(f"mock-ollama (realistic) listening on http://127.0.0.1:{port}", flush=True)
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()


if __name__ == "__main__":
    main()
