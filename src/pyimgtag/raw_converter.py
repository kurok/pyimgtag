"""RAW image thumbnail extraction using exiftool."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

try:
    import rawpy  # noqa: F401
except ImportError:
    rawpy = None  # type: ignore[assignment]

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
        RuntimeError: If exiftool is not available on PATH, or if no embedded
            JPEG could be extracted after trying all tags.
        FileNotFoundError: If the input file does not exist.
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
        try:
            proc = subprocess.run(
                ["exiftool", "-b", f"-{tag}", str(input_path)],
                capture_output=True,
                timeout=30,
            )
        except (subprocess.TimeoutExpired, OSError):
            continue

        if proc.returncode != 0:
            # Non-zero may mean the tag doesn't exist (rc=1) or a real error (rc=2+).
            # Either way, try the next tag — if all fail the final raise gives context.
            continue

        if proc.stdout:
            output_path.write_bytes(proc.stdout)
            return output_path
        # Empty stdout = tag absent, try next

    raise RuntimeError(f"No embedded JPEG found in {input_path}")


def rawpy_available() -> bool:
    """Return True if rawpy is installed (install with: pip install pyimgtag[raw])."""
    return rawpy is not None


def convert_raw_with_rawpy(
    file_path: str | Path,
    output_dir: str | Path | None = None,
) -> Path:
    """Convert a RAW image to JPEG using rawpy for full-quality demosaicing.

    Reads the RAW file with ``rawpy``, applies camera white balance, and saves
    the result as a JPEG at quality 85.

    Args:
        file_path: Path to the RAW input file.
        output_dir: Directory for the output JPEG.  A temporary directory is
            created (prefixed ``pyimgtag_raw_``) when ``None``.

    Returns:
        Path to the converted JPEG file.

    Raises:
        RuntimeError: If rawpy is not installed.  Install it with
            ``pip install pyimgtag[raw]``.
        FileNotFoundError: If the input file does not exist.
    """
    if not rawpy_available():
        raise RuntimeError("rawpy is not installed — install it with: pip install pyimgtag[raw]")

    input_path = Path(file_path)
    if not input_path.is_file():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="pyimgtag_raw_"))
    else:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / f"{input_path.stem}_raw.jpg"

    import rawpy  # noqa: F811  # guarded by rawpy_available() above
    from PIL import Image

    with rawpy.imread(str(input_path)) as raw:
        rgb = raw.postprocess(use_camera_wb=True, output_bps=8)

    image = Image.fromarray(rgb)
    image.save(output_path, format="JPEG", quality=85)

    return output_path
