"""A failed model call reads as one short, actionable line (#433).

The raw requests/urllib3 text moves to a second line: shown by --verbose and
kept in the DB row, never cut mid-word in the default output.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests
from PIL import Image

from pyimgtag.commands.run import _print_brief, _print_verbose, _shorten
from pyimgtag.models import ImageResult
from pyimgtag.ollama_client import OllamaClient, describe_ollama_error

_URL = "http://127.0.0.1:9"


def _http_error(code: int, reason: str, body: object = None) -> requests.HTTPError:
    response = MagicMock(status_code=code, reason=reason)
    if body is None:
        response.json.side_effect = ValueError("no JSON body")
    else:
        response.json.return_value = body
    return requests.HTTPError(
        f"{code} Server Error: {reason} for url: {_URL}/api/chat", response=response
    )


@pytest.mark.parametrize(
    ("exc", "summary"),
    [
        (
            requests.ConnectionError(
                "HTTPConnectionPool(host='127.0.0.1', port=9): Max retries exceeded "
                "with url: /api/chat (Caused by NewConnectionError('Connection refused'))"
            ),
            f"Ollama is not reachable at {_URL} — start it with 'ollama serve'",
        ),
        (
            _http_error(500, "Internal Server Error"),
            "Ollama returned HTTP 500 (Internal Server Error) — is the model loaded? (ollama ps)",
        ),
        (
            _http_error(404, "Not Found"),
            "Ollama returned HTTP 404 (Not Found) — is the model pulled? (ollama pull gemma4)",
        ),
        (
            _http_error(
                500, "Internal Server Error", {"error": "model requires more system memory"}
            ),
            "Ollama returned HTTP 500 (Internal Server Error): model requires more system memory",
        ),
        (
            requests.ConnectTimeout("Connection to 10.0.0.9 timed out. (connect timeout=30)"),
            f"Ollama is not reachable at {_URL} — start it with 'ollama serve'",
        ),
        (
            requests.Timeout("Read timed out. (read timeout=30)"),
            "Ollama did not answer within 30s — the model may still be loading; retry, "
            "or raise --timeout",
        ),
    ],
)
def test_each_failure_has_a_readable_first_line_and_keeps_the_raw_detail(exc, summary):
    message = describe_ollama_error(exc, _URL, "gemma4", 30)
    first, _, detail = message.partition("\n")
    assert first == summary
    assert "HTTPConnectionPool" not in first
    assert detail == f"{type(exc).__name__}: {exc}"


def test_tag_image_uses_the_readable_message(tmp_path):
    photo = tmp_path / "a.jpg"
    Image.new("RGB", (8, 8)).save(photo)
    client = OllamaClient(model="gemma4", base_url=_URL, timeout=30)
    refused = requests.ConnectionError("HTTPConnectionPool(host='127.0.0.1', port=9): refused")
    with patch.object(client._session, "post", side_effect=refused):
        result = client.tag_image(str(photo))
    assert result.error is not None
    assert result.error.startswith(f"Ollama is not reachable at {_URL}")
    assert result.error.endswith(
        "ConnectionError: HTTPConnectionPool(host='127.0.0.1', port=9): refused"
    )


@pytest.mark.parametrize(
    ("text", "width", "expected"),
    [
        ("short", 20, "short"),
        ("Ollama returned HTTP 500 (Internal Server Error)", 30, "Ollama returned HTTP 500…"),
        ("abcdefghijklmnopqrstuvwxyz", 10, "abcdefghi…"),
        ("one two, three", 12, "one two…"),
        # The cut lands exactly on a space: the whole word before it is kept.
        ("x" * 9 + " " + "y" * 20, 10, "x" * 9 + "…"),
        ("abc", 1, "…"),
    ],
)
def test_shorten_cuts_on_a_word_boundary_and_marks_it(text, width, expected):
    out = _shorten(text, width)
    assert out == expected
    assert len(out) <= width


def _failed(message: str) -> ImageResult:
    return ImageResult(
        file_path="/p/x.jpg", file_name="x.jpg", processing_status="error", error_message=message
    )


def test_brief_output_shows_only_the_readable_line(capsys):
    message = describe_ollama_error(_http_error(500, "Internal Server Error"), _URL, "m", 30)
    _print_brief(_failed(message), 1, 6)
    captured = capsys.readouterr()
    line = captured.out + captured.err
    assert line == (
        "[1/6] x.jpg -> (none) | error: Ollama returned HTTP 500 (Internal Server Error) "
        "— is the model loaded? (ollama ps)\n"
    )


def test_verbose_output_shows_the_detail_too(capsys):
    message = describe_ollama_error(_http_error(500, "Internal Server Error"), _URL, "m", 30)
    _print_verbose(_failed(message), 1, 6)
    err = capsys.readouterr().err
    assert "  Error:    Ollama returned HTTP 500 (Internal Server Error)" in err
    assert f"  Detail:   HTTPError: 500 Server Error: Internal Server Error for url: {_URL}" in err


def test_cloud_tag_failures_get_the_same_two_line_shape(tmp_path):
    from pyimgtag.cloud_clients import AnthropicClient

    photo = tmp_path / "a.jpg"
    Image.new("RGB", (8, 8)).save(photo)
    client = AnthropicClient(api_key="x")
    raw = "HTTPSConnectionPool(host='api.anthropic.com', port=443): Max retries exceeded"
    with patch.object(client._session, "post", side_effect=requests.ConnectionError(raw)):
        result = client.tag_image(str(photo))
    first, _, detail = (result.error or "").partition("\n")
    assert first == "anthropic request failed: could not connect"
    assert detail == f"ConnectionError: {raw}"
