"""HEIC to JPEG conversion using macOS sips command."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

_HEIC_EXTENSIONS = {".heic", ".heif"}


def is_heic(file_path: str | Path) -> bool:
    """Check if a file has an HEIC/HEIF extension (case-insensitive)."""
    return Path(file_path).suffix.lower() in _HEIC_EXTENSIONS


def sips_available() -> bool:
    """Check if the macOS sips command is available on this system."""
    return shutil.which("sips") is not None


def convert_heic_to_jpeg(
    file_path: str | Path,
    output_dir: str | Path | None = None,
) -> Path:
    """Convert an HEIC/HEIF file to JPEG using macOS sips.

    Args:
        file_path: Path to the HEIC/HEIF input file.
        output_dir: Directory for the output JPEG. If None, a temporary
            directory is created via ``tempfile.mkdtemp()``.

    Returns:
        Path to the converted JPEG file.

    Raises:
        RuntimeError: If sips is not available or conversion fails.
        FileNotFoundError: If the input file does not exist.
    """
    if not sips_available():
        raise RuntimeError("sips is not available (macOS only)")

    input_path = Path(file_path)
    if not input_path.is_file():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    _owned_temp_dir: str | None = None
    if output_dir is None:
        _owned_temp_dir = tempfile.mkdtemp(prefix="pyimgtag_heic_")
        output_dir = Path(_owned_temp_dir)
    else:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / (input_path.stem + ".jpg")

    try:
        proc = subprocess.run(
            ["sips", "-s", "format", "jpeg", str(input_path), "--out", str(output_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        if _owned_temp_dir is not None:
            shutil.rmtree(_owned_temp_dir, ignore_errors=True)
        raise RuntimeError(f"sips conversion timed out for {input_path}") from exc
    except Exception:
        if _owned_temp_dir is not None:
            shutil.rmtree(_owned_temp_dir, ignore_errors=True)
        raise

    if proc.returncode != 0:
        raise RuntimeError(f"sips conversion failed (rc={proc.returncode}): {proc.stderr.strip()}")

    if not output_path.is_file():
        raise RuntimeError(f"sips did not produce output file: {output_path}")

    return output_path
