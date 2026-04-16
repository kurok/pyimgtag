"""RAW image thumbnail extraction using exiftool."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

RAW_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".cr2",
        ".cr3",
        ".nef",
        ".nrw",
        ".arw",
        ".sr2",
        ".srf",
        ".raf",
        ".orf",
        ".rw2",
        ".pef",
        ".3fr",
        ".fff",
        ".rwl",
        ".dng",
    }
)

_THUMBNAIL_TAGS = ("JpgFromRaw", "PreviewImage", "ThumbnailImage")


def is_raw(file_path: str | Path) -> bool:
    """Return True if the file has a known RAW extension.

    Args:
        file_path: Path to the file to check.

    Returns:
        True if the file extension is a recognised RAW format.
    """
    return Path(file_path).suffix.lower() in RAW_EXTENSIONS


def extract_raw_thumbnail(
    file_path: str | Path,
    output_dir: str | Path | None = None,
) -> Path:
    """Extract an embedded JPEG thumbnail from a RAW file using exiftool.

    Tries the tags ``JpgFromRaw``, ``PreviewImage``, and ``ThumbnailImage``
    in order and writes the first non-empty result to disk.

    Args:
        file_path: Path to the RAW input file.
        output_dir: Directory for the output JPEG.  A temporary directory is
            created when ``None``.

    Returns:
        Path to the extracted JPEG thumbnail.

    Raises:
        RuntimeError: If exiftool is not available on PATH.
        FileNotFoundError: If the input file does not exist.
        RuntimeError: If no embedded JPEG could be extracted.
    """
    if shutil.which("exiftool") is None:
        raise RuntimeError("exiftool is not available — install it and add it to PATH")

    input_path = Path(file_path)
    if not input_path.is_file():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="pyimgtag_raw_"))
    else:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / f"{input_path.stem}_thumb.jpg"

    for tag in _THUMBNAIL_TAGS:
        proc = subprocess.run(
            ["exiftool", "-b", f"-{tag}", str(input_path)],
            capture_output=True,
            timeout=30,
        )
        if proc.returncode == 0 and proc.stdout:
            output_path.write_bytes(proc.stdout)
            return output_path

    raise RuntimeError(f"No embedded JPEG found in {input_path}")
