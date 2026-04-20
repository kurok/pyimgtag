"""Tests for examples/mock_ollama.py to ensure import safety."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def test_mock_ollama_import_safe_with_arbitrary_argv() -> None:
    """
    Test that examples/mock_ollama.py can be imported safely with arbitrary
    sys.argv values (like those from pytest collection).

    This test uses importlib to load the module with mocked sys.argv values
    that would previously cause a ValueError at import time.
    """
    mock_ollama_path = Path(__file__).parent.parent / "examples" / "mock_ollama.py"
    assert mock_ollama_path.exists(), f"mock_ollama.py not found at {mock_ollama_path}"

    # Simulate pytest collection with arbitrary command-line arguments
    original_argv = sys.argv
    try:
        sys.argv = ["pytest", "-q", "-x"]

        spec = importlib.util.spec_from_file_location("mock_ollama_test", str(mock_ollama_path))
        assert spec is not None, "Failed to create module spec"
        assert spec.loader is not None, "Failed to get module loader"

        mock_ollama = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mock_ollama)

        # Verify the module loaded successfully and DEFAULT_PORT is accessible
        assert hasattr(mock_ollama, "DEFAULT_PORT")
        assert mock_ollama.DEFAULT_PORT == 11435

    finally:
        sys.argv = original_argv


def test_mock_ollama_default_port_constant() -> None:
    """Test that DEFAULT_PORT constant is defined and has the correct value."""
    mock_ollama_path = Path(__file__).parent.parent / "examples" / "mock_ollama.py"

    original_argv = sys.argv
    try:
        sys.argv = ["python3", "mock_ollama.py"]

        spec = importlib.util.spec_from_file_location("mock_ollama_test2", str(mock_ollama_path))
        assert spec is not None
        assert spec.loader is not None

        mock_ollama = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mock_ollama)

        assert mock_ollama.DEFAULT_PORT == 11435

    finally:
        sys.argv = original_argv
