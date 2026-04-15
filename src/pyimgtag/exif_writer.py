"""Write description and keywords back to image EXIF metadata via exiftool."""

from __future__ import annotations

import shutil
import subprocess


def write_exif_description(
    file_path: str,
    description: str | None = None,
    keywords: list[str] | None = None,
) -> str | None:
    """Write description and/or keywords to image EXIF using exiftool.

    Sets the following EXIF/IPTC/XMP fields:
    - ImageDescription, XMP:Description, IPTC:Caption-Abstract (description)
    - IPTC:Keywords, XMP:Subject (keywords)

    Args:
        file_path: Path to the image file.
        description: Description text to write. Skipped when None.
        keywords: List of keyword strings. Skipped when None or empty.

    Returns:
        None on success, or an error message string on failure.
    """
    if not is_exiftool_available():
        return "exiftool is not available on this system"

    if description is None and not keywords:
        return None

    args = ["exiftool", "-overwrite_original"]

    if description is not None:
        args.append(f"-ImageDescription={description}")
        args.append(f"-XMP:Description={description}")
        args.append(f"-IPTC:Caption-Abstract={description}")

    if keywords:
        # Clear existing keywords first, then set new ones
        args.append("-IPTC:Keywords=")
        args.append("-XMP:Subject=")
        for kw in keywords:
            args.append(f"-IPTC:Keywords={kw}")
            args.append(f"-XMP:Subject={kw}")

    args.append(file_path)

    try:
        proc = subprocess.run(  # noqa: S603
            args,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return "exiftool timed out after 30 seconds"
    except OSError as exc:
        return f"Failed to launch exiftool: {exc}"

    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        return (
            f"exiftool error (exit {proc.returncode}): {stderr}"
            if stderr
            else f"exiftool failed with exit code {proc.returncode}"
        )

    return None


def is_exiftool_available() -> bool:
    """Return True if exiftool is available on this system."""
    return shutil.which("exiftool") is not None
