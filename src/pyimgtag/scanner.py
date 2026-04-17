"""File scanning for directories and Apple Photos library packages."""

from __future__ import annotations

from pathlib import Path

DEFAULT_EXTENSIONS = {"jpg", "jpeg", "heic", "png"}

_FDA_HINT = (
    "Grant Full Disk Access to Terminal in System Settings → Privacy & Security → Full Disk Access."
)


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

    Raises:
        FileNotFoundError: Library or originals directory not found.
        PermissionError: macOS TCC prevents reading the library contents.
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

    files = sorted(
        e for e in originals.rglob("*") if e.is_file() and e.suffix.lstrip(".").lower() in exts
    )

    if not files:
        # rglob silently skips directories it cannot read (macOS TCC blocks listdir
        # even when stat succeeds, so is_dir() passes but the contents are invisible).
        # Surface the real PermissionError so the user gets a useful message.
        _assert_readable(originals)

    return files


def _assert_readable(originals: Path) -> None:
    """Raise PermissionError with a Full Disk Access hint if originals is unreadable."""
    try:
        entries = list(originals.iterdir())
    except PermissionError as exc:
        raise PermissionError(
            f"Cannot read Photos library originals at {originals}: permission denied. " + _FDA_HINT
        ) from exc

    # Also probe one subdirectory — rglob will silently skip these if unreadable.
    for entry in entries:
        if entry.is_dir():
            try:
                next(iter(entry.iterdir()), None)
            except PermissionError as exc:
                raise PermissionError(
                    f"Cannot read Photos library originals at {originals}: "
                    f"permission denied on subdirectory {entry.name}/. " + _FDA_HINT
                ) from exc
            break
