#!/usr/bin/env python3
"""
Minimal mock Ollama server for pyimgtag demos and output capture.

Usage:
    python3 mock_ollama.py [PORT]
    # defaults to port 11435 to avoid conflict with a real Ollama instance

Responds to POST /api/chat with canned TagResult JSON and to
GET /api/tags with a minimal model listing so pyimgtag preflight
and check_ollama_model succeed against the mock.
"""

from __future__ import annotations

import contextlib
import http.server
import json
import sys
import threading
from typing import Any

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 11435

_RESPONSES: list[dict[str, Any]] = [
    {
        "tags": ["sunset", "beach", "ocean", "waves", "golden-hour"],
        "summary": "golden hour sunset over the Pacific Ocean",
        "scene_category": "outdoor_leisure",
        "emotional_tone": "positive",
        "cleanup_class": "keep",
        "has_text": False,
        "text_summary": None,
        "event_hint": "outing",
        "significance": "high",
    },
    {
        "tags": ["street", "architecture", "city", "people"],
        "summary": "busy Paris street with classic Haussmann buildings",
        "scene_category": "outdoor_travel",
        "emotional_tone": "neutral",
        "cleanup_class": "keep",
        "has_text": False,
        "text_summary": None,
        "event_hint": "travel",
        "significance": "medium",
    },
    {
        "tags": ["food", "dinner", "candles", "family"],
        "summary": "family dinner with candles on a wooden table",
        "scene_category": "indoor_home",
        "emotional_tone": "positive",
        "cleanup_class": "keep",
        "has_text": False,
        "text_summary": None,
        "event_hint": "gathering",
        "significance": "high",
    },
    {
        "tags": ["screenshot", "text", "blurry"],
        "summary": "blurry screenshot of a webpage",
        "scene_category": "other",
        "emotional_tone": "neutral",
        "cleanup_class": "delete",
        "has_text": True,
        "text_summary": "webpage content, partially readable",
        "event_hint": "daily",
        "significance": "low",
    },
    {
        "tags": ["mountain", "hiking", "snow", "alpine", "trail"],
        "summary": "alpine trail with snow-capped peaks in the background",
        "scene_category": "outdoor_leisure",
        "emotional_tone": "positive",
        "cleanup_class": "keep",
        "has_text": False,
        "text_summary": None,
        "event_hint": "outing",
        "significance": "high",
    },
    {
        "tags": ["office", "meeting", "laptop", "whiteboard"],
        "summary": "office meeting room with whiteboard and laptops",
        "scene_category": "indoor_work",
        "emotional_tone": "neutral",
        "cleanup_class": "review",
        "has_text": True,
        "text_summary": "Q2 roadmap items on whiteboard",
        "event_hint": "work",
        "significance": "medium",
    },
]

_counter: list[int] = [0]
_lock = threading.Lock()

# Mirrors the shape returned by a real Ollama server at GET /api/tags so that
# pyimgtag's check_ollama() and check_ollama_model() succeed against the mock.
_TAGS_RESPONSE: dict[str, Any] = {
    "models": [
        {
            "name": "gemma4:e4b",
            "model": "gemma4:e4b",
            "size": 0,
            "digest": "mock",
            "details": {"family": "gemma", "parameter_size": "4B"},
        }
    ]
}


class MockOllamaHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == "/api/tags":
            body = json.dumps(_TAGS_RESPONSE).encode()
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
        self.rfile.read(length)

        with _lock:
            resp = _RESPONSES[_counter[0] % len(_RESPONSES)]
            _counter[0] += 1

        body = json.dumps({"message": {"content": json.dumps(resp)}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        pass


if __name__ == "__main__":
    server = http.server.HTTPServer(("127.0.0.1", PORT), MockOllamaHandler)
    print(f"mock-ollama listening on http://127.0.0.1:{PORT}", flush=True)
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()
