"""File scanning for directories and Apple Photos library packages."""

from __future__ import annotations

from pathlib import Path

DEFAULT_EXTENSIONS = {"jpg", "jpeg", "heic", "png"}


def scan_directory(
    path: str | Path,
    extensions: set[str] | None = None,
    recursive: bool = True,
) -> list[Path]:
    """Scan a directory for image files, sorted by name.

    Args:
        path: Directory to scan.
        extensions: File extensions to include (without dots).
        recursive: When True (default), scan subdirectories recursively.
    """
    exts = extensions or DEFAULT_EXTENSIONS
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Directory not found: {root}")
    pattern = "*" if not recursive else "**/*"
    return sorted(
        e for e in root.glob(pattern) if e.is_file() and e.suffix.lstrip(".").lower() in exts
    )


def scan_photos_library(library_path: str | Path, extensions: set[str] | None = None) -> list[Path]:
    """Best-effort scan of originals inside an Apple Photos library package.

    Tries ``originals/`` first (modern format), then ``Masters/`` (older format).
    """
    exts = extensions or DEFAULT_EXTENSIONS
    root = Path(library_path).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Photos library not found: {root}")

    originals = root / "originals"
    if not originals.is_dir():
        originals = root / "Masters"
    if not originals.is_dir():
        raise FileNotFoundError(
            f"Cannot find originals directory in Photos library: {root}. "
            "Tried 'originals/' and 'Masters/'."
        )
    return sorted(
        e for e in originals.rglob("*") if e.is_file() and e.suffix.lstrip(".").lower() in exts
    )
