"""Preflight checks for pyimgtag prerequisites."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import requests


def check_ollama(base_url: str = "http://localhost:11434") -> tuple[bool, str]:
    """Check that the Ollama server is reachable.

    Args:
        base_url: Ollama HTTP base URL.

    Returns:
        Tuple of (success, message).
    """
    try:
        resp = requests.get(f"{base_url.rstrip('/')}/api/tags", timeout=5)
        resp.raise_for_status()
        models = resp.json().get("models", [])
        return (True, f"Ollama is running ({len(models)} models available)")
    except requests.RequestException as e:
        return (False, f"Ollama is not reachable at {base_url}: {e}")


def check_cloud_backend(backend: str) -> tuple[bool, str]:
    """Check that the API key for a cloud backend is present in the env.

    No network call is made — this verifies only that the user has set the
    expected env var, which surfaces the most common misconfiguration.
    """
    env_vars = {
        "anthropic": ("ANTHROPIC_API_KEY",),
        "openai": ("OPENAI_API_KEY",),
        "gemini": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
    }.get(backend)
    if env_vars is None:
        return (False, f"Unknown backend: {backend}")
    for var in env_vars:
        if os.environ.get(var, "").strip():
            return (True, f"{backend}: {var} is set")
    joined = " or ".join(env_vars)
    return (False, f"{backend}: no API key — set {joined}")


def check_ollama_model(model: str, base_url: str = "http://localhost:11434") -> tuple[bool, str]:
    """Check that a specific model is available in Ollama.

    Args:
        model: Model name to look for.
        base_url: Ollama HTTP base URL.

    Returns:
        Tuple of (success, message).
    """
    try:
        resp = requests.get(f"{base_url.rstrip('/')}/api/tags", timeout=5)
        resp.raise_for_status()
        models = resp.json().get("models", [])
        names = [m.get("name", "") for m in models]
        if model in names:
            return (True, f"Model '{model}' is available")
        return (False, f"Model '{model}' not found. Available: {names}")
    except requests.RequestException as e:
        return (False, f"Cannot check model: Ollama not reachable ({e})")


def check_exiftool() -> tuple[bool, str]:
    """Check that exiftool is installed and working.

    Returns:
        Tuple of (success, message).
    """
    try:
        result = subprocess.run(
            ["exiftool", "-ver"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        version = result.stdout.strip()
        return (True, f"exiftool {version} is installed")
    except FileNotFoundError:
        return (
            False,
            "exiftool is not installed. See https://exiftool.org for install instructions.",
        )
    except (OSError, subprocess.SubprocessError) as e:
        return (False, f"exiftool check failed: {e}")


def check_photos_library(library_path: str) -> tuple[bool, str]:
    """Check that an Apple Photos library is accessible.

    Args:
        library_path: Path to the .photoslibrary package.

    Returns:
        Tuple of (success, message).
    """
    root = Path(library_path).expanduser().resolve()
    if not root.is_dir():
        return (False, f"Photos library not found: {root}")

    originals = root / "originals"
    if not originals.is_dir():
        originals = root / "Masters"
    if not originals.is_dir():
        return (
            False,
            f"Cannot find originals directory in Photos library: {root}. "
            "Tried 'originals/' and 'Masters/'.",
        )

    try:
        count = sum(1 for f in originals.rglob("*") if f.is_file())
    except PermissionError as e:
        return (False, f"Cannot read Photos library originals: {e}")
    return (True, f"Photos library accessible ({count} files in originals)")


def check_directory(dir_path: str) -> tuple[bool, str]:
    """Check that a directory exists and is readable.

    Args:
        dir_path: Path to the directory.

    Returns:
        Tuple of (success, message).
    """
    root = Path(dir_path).expanduser().resolve()
    if not root.exists():
        return (False, f"Directory not found: {root}")
    if not root.is_dir():
        return (False, f"Not a directory: {root}")
    if not os.access(root, os.R_OK):
        return (False, f"Directory not readable: {root}")

    try:
        count = sum(1 for f in root.rglob("*") if f.is_file())
    except PermissionError as e:
        return (False, f"Cannot read directory: {e}")
    return (True, f"Directory accessible ({count} files)")


def run_preflight(
    ollama_url: str,
    model: str,
    source_path: str | None = None,
    source_type: str = "directory",
) -> list[tuple[str, bool, str]]:
    """Run all applicable preflight checks.

    Args:
        ollama_url: Ollama HTTP base URL.
        model: Ollama model name.
        source_path: Optional path to image source directory or Photos library.
        source_type: Either ``"directory"`` or ``"photos_library"``.

    Returns:
        List of ``(check_name, passed, message)`` tuples.
    """
    results: list[tuple[str, bool, str]] = []

    passed, msg = check_ollama(ollama_url)
    results.append(("Ollama", passed, msg))

    passed, msg = check_ollama_model(model, ollama_url)
    results.append(("Ollama model", passed, msg))

    passed, msg = check_exiftool()
    results.append(("exiftool", passed, msg))

    if source_path is not None:
        if source_type == "photos_library":
            passed, msg = check_photos_library(source_path)
            results.append(("Photos library", passed, msg))
        else:
            passed, msg = check_directory(source_path)
            results.append(("Directory", passed, msg))

    return results
