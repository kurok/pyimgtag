"""One client factory, one retry policy, one place for the Ollama defaults (#401)."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
from PIL import Image

from pyimgtag.ollama_client import DEFAULT_OLLAMA_MODEL, DEFAULT_OLLAMA_URL, OllamaClient

_SRC = Path(__file__).resolve().parent.parent / "src" / "pyimgtag"
_TAG_JSON = (
    '{"tags":["beach"],"summary":"A beach.","scene_category":"outdoor_leisure",'
    '"emotional_tone":"positive","cleanup_class":"keep","has_text":false,'
    '"text_summary":null,"event_hint":"outing","significance":"low"}'
)


def _resp(status: int, content: str = _TAG_JSON, headers: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.headers = headers or {}
    resp.json.return_value = {"message": {"content": content}}
    if status >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(f"{status} error", response=resp)
    else:
        resp.raise_for_status.return_value = None
    return resp


@pytest.fixture
def jpg(tmp_path: Path) -> str:
    path = tmp_path / "a.jpg"
    Image.new("RGB", (16, 16)).save(path)
    return str(path)


def _client() -> OllamaClient:
    return OllamaClient(model="m", base_url="http://127.0.0.1:1")


class TestOllamaRetries:
    def test_503_503_200_succeeds_after_two_backoffs(self, jpg):
        client = _client()
        replies = [_resp(503), _resp(503), _resp(200)]
        with (
            patch.object(client._session, "post", side_effect=replies) as post,
            patch("pyimgtag._http_retry.time.sleep") as sleep,
        ):
            result = client.tag_image(jpg)
        assert result.error is None
        assert result.tags == ["beach"]
        assert post.call_count == 3
        assert sleep.call_count == 2

    def test_five_429s_fail_with_a_rate_limit_message(self, jpg):
        client = _client()
        with (
            patch.object(client._session, "post", side_effect=[_resp(429)] * 5) as post,
            patch("pyimgtag._http_retry.time.sleep") as sleep,
        ):
            result = client.tag_image(jpg)
        assert post.call_count == 5
        assert sleep.call_count == 4
        assert result.error and "rate limited" in result.error

    def test_retry_after_is_honoured(self, jpg):
        client = _client()
        replies = [_resp(429, headers={"Retry-After": "7"}), _resp(200)]
        with (
            patch.object(client._session, "post", side_effect=replies),
            patch("pyimgtag._http_retry.time.sleep") as sleep,
        ):
            assert client.tag_image(jpg).error is None
        sleep.assert_called_once_with(7.0)

    def test_a_500_is_not_retried(self, jpg):
        client = _client()
        with (
            patch.object(client._session, "post", side_effect=[_resp(500), _resp(200)]) as post,
            patch("pyimgtag._http_retry.time.sleep") as sleep,
        ):
            result = client.tag_image(jpg)
        assert post.call_count == 1
        sleep.assert_not_called()
        assert result.error and "500" in result.error

    def test_judge_and_generate_text_retry_too(self, jpg):
        client = _client()
        judge_reply = _resp(200, content='{"score": 7, "reason": "ok"}')
        with (
            patch.object(client._session, "post", side_effect=[_resp(503), judge_reply]),
            patch("pyimgtag._http_retry.time.sleep"),
        ):
            assert client.judge_image(jpg) is not None
        text_reply = _resp(200, content="Summer at the lake")
        with (
            patch.object(client._session, "post", side_effect=[_resp(429), text_reply]) as post,
            patch("pyimgtag._http_retry.time.sleep"),
        ):
            assert client.generate_text("name this") == "Summer at the lake"
        assert post.call_count == 2

    def test_every_attempt_takes_a_rate_limit_token(self, jpg):
        client = _client()
        with (
            patch.object(client._session, "post", side_effect=[_resp(503), _resp(200)]),
            patch("pyimgtag._http_retry.time.sleep"),
            patch("pyimgtag._http_retry.acquire_global_rate_limit") as token,
        ):
            client.tag_image(jpg)
        assert token.call_count == 2


def _run_args(**overrides) -> argparse.Namespace:
    values = dict(
        model=None,
        max_dim=640,
        timeout=9,
        api_key=None,
        api_base="https://ignored.example",
        ollama_url="http://ollama.lan:11434",
    )
    values.update(overrides)
    return argparse.Namespace(**values)


@pytest.mark.parametrize("backend", ["ollama", "anthropic"])
def test_run_builds_its_client_through_the_factory(backend):
    from pyimgtag.commands import run

    with patch("pyimgtag.commands.run.make_image_client") as factory:
        run._create_image_client(_run_args(), backend)
    factory.assert_called_once()
    args, kwargs = factory.call_args
    assert args == (backend,)
    expected_base = "http://ollama.lan:11434" if backend == "ollama" else "https://ignored.example"
    assert kwargs["api_base"] == expected_base
    assert kwargs["ollama_class"] is run.OllamaClient


def test_judge_builds_its_client_through_the_factory(tmp_path):
    from pyimgtag.commands import judge

    args = argparse.Namespace(
        backend="ollama",
        ollama_url="http://ollama.lan:11434",
        model=None,
        max_dim=640,
        timeout=9,
        api_key=None,
        api_base=None,
        extensions="jpg",
        input_dir=str(tmp_path),
        photos_library=None,
        limit=None,
        min_score=None,
        sort_by="score",
        output_json=None,
        verbose=False,
        write_back=False,
        write_back_mode="overwrite",
        no_recursive=False,
        web=False,
        no_web=True,
        web_host="127.0.0.1",
        web_port=8770,
        no_browser=True,
    )
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"x")
    with (
        patch("pyimgtag.commands.judge.check_ollama", return_value=(True, "")),
        patch("pyimgtag.commands.judge.scan_directory", return_value=[photo]),
        patch("pyimgtag.commands.judge.make_image_client") as factory,
    ):
        factory.return_value.judge_image.return_value = None
        factory.return_value.last_error = "request failed: HTTP 500"
        judge.cmd_judge(args, MagicMock())
    factory.assert_called_once()
    assert factory.call_args.args == ("ollama",)
    assert factory.call_args.kwargs["api_base"] == "http://ollama.lan:11434"
    assert factory.call_args.kwargs["ollama_class"] is judge.OllamaClient


def test_the_factory_uses_the_shared_defaults():
    from pyimgtag.cloud_clients import make_image_client

    client = make_image_client("ollama")
    assert (client.model, client.base_url) == (DEFAULT_OLLAMA_MODEL, DEFAULT_OLLAMA_URL)
    client.close()


@pytest.mark.parametrize("literal", ["gemma4:e4b", "localhost:11434"])
def test_each_default_is_written_in_exactly_one_source_file(literal):
    files = sorted(
        str(p.relative_to(_SRC)) for p in _SRC.rglob("*.py") if literal in p.read_text("utf-8")
    )
    assert files == ["ollama_defaults.py"]


def test_ollama_url_from_env(monkeypatch):
    from pyimgtag.ollama_defaults import ollama_url_from_env

    monkeypatch.delenv("OLLAMA_URL", raising=False)
    assert ollama_url_from_env() == DEFAULT_OLLAMA_URL
    monkeypatch.setenv("OLLAMA_URL", "http://gpu-box:11434")
    assert ollama_url_from_env() == "http://gpu-box:11434"


def test_importing_the_cli_stays_light():
    """The defaults module must not pull Pillow or requests into ``main``."""
    import subprocess
    import sys

    code = (
        "import sys, pyimgtag.main; "
        "print(int('PIL' in sys.modules), int('requests' in sys.modules))"
    )
    out = subprocess.run(  # nosec B603 — fixed interpreter
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout.split()
    assert out == ["0", "0"]
