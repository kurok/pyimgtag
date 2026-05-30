"""Tests for preflight checks."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from pyimgtag.preflight import (
    check_cloud_backend,
    check_directory,
    check_exiftool,
    check_ollama,
    check_ollama_model,
    check_photos_library,
    run_preflight,
)


class TestCheckOllama:
    @patch("pyimgtag.preflight.requests.get")
    def test_success(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "models": [
                {"name": "gemma4:e4b"},
                {"name": "llama3:8b"},
                {"name": "mistral:7b"},
            ]
        }
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        ok, msg = check_ollama()
        assert ok is True
        assert "3 models available" in msg

    @patch("pyimgtag.preflight.requests.get")
    def test_connection_error(self, mock_get: MagicMock) -> None:
        import requests

        mock_get.side_effect = requests.ConnectionError("Connection refused")

        ok, msg = check_ollama()
        assert ok is False
        assert "not reachable" in msg

    @patch("pyimgtag.preflight.requests.get")
    def test_request_exception_returns_fail(self, mock_get: MagicMock) -> None:
        import requests

        mock_get.side_effect = requests.RequestException("timeout")

        ok, msg = check_ollama()
        assert ok is False
        assert "not reachable" in msg


class TestCheckOllamaModel:
    @patch("pyimgtag.preflight.requests.get")
    def test_model_found(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "models": [
                {"name": "gemma4:e4b"},
                {"name": "llama3:8b"},
            ]
        }
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        ok, msg = check_ollama_model("gemma4:e4b")
        assert ok is True
        assert "gemma4:e4b" in msg
        assert "available" in msg

    @patch("pyimgtag.preflight.requests.get")
    def test_model_not_found(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "models": [
                {"name": "llama3:8b"},
            ]
        }
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        ok, msg = check_ollama_model("gemma4:e4b")
        assert ok is False
        assert "not found" in msg
        assert "llama3:8b" in msg

    @patch("pyimgtag.preflight.requests.get")
    def test_request_exception_returns_fail(self, mock_get: MagicMock) -> None:
        import requests

        mock_get.side_effect = requests.RequestException("connection refused")

        ok, msg = check_ollama_model("gemma4:e4b")
        assert ok is False
        assert "not reachable" in msg


class TestCheckExiftool:
    @patch("pyimgtag.preflight.subprocess.run")
    def test_success(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["exiftool", "-ver"],
            returncode=0,
            stdout="12.76\n",
            stderr="",
        )

        ok, msg = check_exiftool()
        assert ok is True
        assert "12.76" in msg

    @patch("pyimgtag.preflight.subprocess.run")
    def test_not_installed(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = FileNotFoundError("No such file or directory")

        ok, msg = check_exiftool()
        assert ok is False
        assert "not installed" in msg
        assert "https://exiftool.org for install instructions" in msg  # cross-platform hint
        assert "brew install" not in msg  # no macOS-only hint

    @patch("pyimgtag.preflight.subprocess.run")
    def test_present_but_broken_returns_fail(self, mock_run: MagicMock) -> None:
        # exiftool exists but '-ver' exits nonzero (broken install / lib error);
        # the gate must not green-light it with a blank version.
        mock_run.return_value = subprocess.CompletedProcess(
            args=["exiftool", "-ver"],
            returncode=2,
            stdout="",
            stderr="Can't locate Image/ExifTool.pm",
        )

        ok, msg = check_exiftool()
        assert ok is False
        assert "exit 2" in msg
        assert "Image/ExifTool.pm" in msg
        assert "is installed" not in msg


class TestCheckPhotosLibrary:
    def test_valid_structure(self, tmp_path: Path) -> None:
        originals = tmp_path / "originals"
        originals.mkdir()
        (originals / "photo1.jpg").write_text("fake")
        (originals / "photo2.jpg").write_text("fake")

        ok, msg = check_photos_library(str(tmp_path))
        assert ok is True
        assert "2 files" in msg

    def test_missing_originals(self, tmp_path: Path) -> None:
        ok, msg = check_photos_library(str(tmp_path))
        assert ok is False
        assert "Cannot find originals" in msg

    def test_nonexistent_path(self) -> None:
        ok, msg = check_photos_library("/nonexistent/path/12345.photoslibrary")
        assert ok is False
        assert "not found" in msg


class TestCheckDirectory:
    def test_valid_dir(self, tmp_path: Path) -> None:
        (tmp_path / "file1.txt").write_text("hello")
        (tmp_path / "file2.txt").write_text("world")

        ok, msg = check_directory(str(tmp_path))
        assert ok is True
        assert "2 files" in msg

    def test_nonexistent(self) -> None:
        ok, msg = check_directory("/nonexistent/path/12345")
        assert ok is False
        assert "not found" in msg

    def test_permission_error_returns_fail(self, tmp_path: Path) -> None:
        subdir = tmp_path / "locked"
        subdir.mkdir()
        with patch("pyimgtag.preflight.Path.rglob", side_effect=PermissionError("denied")):
            ok, msg = check_directory(str(subdir))
        assert ok is False
        assert "Cannot read directory" in msg


class TestRunPreflight:
    @patch("pyimgtag.preflight.subprocess.run")
    @patch("pyimgtag.preflight.requests.get")
    def test_returns_all_checks(self, mock_get: MagicMock, mock_run: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"models": [{"name": "gemma4:e4b"}]}
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        mock_run.return_value = subprocess.CompletedProcess(
            args=["exiftool", "-ver"], returncode=0, stdout="12.76\n", stderr=""
        )

        results = run_preflight("http://localhost:11434", "gemma4:e4b")

        assert len(results) == 3
        names = [r[0] for r in results]
        assert "Ollama" in names
        assert "Ollama model" in names
        assert "exiftool" in names
        assert all(r[1] is True for r in results)

    @patch("pyimgtag.preflight.subprocess.run")
    @patch("pyimgtag.preflight.requests.get")
    def test_with_directory(self, mock_get: MagicMock, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"models": [{"name": "gemma4:e4b"}]}
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        mock_run.return_value = subprocess.CompletedProcess(
            args=["exiftool", "-ver"], returncode=0, stdout="12.76\n", stderr=""
        )

        (tmp_path / "img.jpg").write_text("fake")

        results = run_preflight("http://localhost:11434", "gemma4:e4b", str(tmp_path), "directory")

        assert len(results) == 4
        names = [r[0] for r in results]
        assert "Directory" in names

    @patch("pyimgtag.preflight.subprocess.run")
    @patch("pyimgtag.preflight.requests.get")
    def test_with_photos_library(
        self, mock_get: MagicMock, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"models": [{"name": "gemma4:e4b"}]}
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        mock_run.return_value = subprocess.CompletedProcess(
            args=["exiftool", "-ver"], returncode=0, stdout="12.76\n", stderr=""
        )

        originals = tmp_path / "originals"
        originals.mkdir()
        (originals / "photo.jpg").write_text("fake")

        results = run_preflight(
            "http://localhost:11434", "gemma4:e4b", str(tmp_path), "photos_library"
        )

        assert len(results) == 4
        names = [r[0] for r in results]
        assert "Photos library" in names


class TestCheckCloudBackend:
    def test_unknown_backend_returns_false(self) -> None:
        ok, msg = check_cloud_backend("bogusbackend")
        assert ok is False
        assert "Unknown backend" in msg

    def test_anthropic_key_present(self, monkeypatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
        ok, msg = check_cloud_backend("anthropic")
        assert ok is True
        assert "ANTHROPIC_API_KEY" in msg

    def test_openai_key_absent(self, monkeypatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        ok, msg = check_cloud_backend("openai")
        assert ok is False
        assert "OPENAI_API_KEY" in msg

    def test_gemini_falls_back_to_gemini_api_key(self, monkeypatch) -> None:
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.setenv("GEMINI_API_KEY", "gm-fallback")
        ok, msg = check_cloud_backend("gemini")
        assert ok is True
        assert "GEMINI_API_KEY" in msg
