"""File scanning for directories and Apple Photos library packages."""

from __future__ import annotations

from pathlib import Path

DEFAULT_EXTENSIONS = {"jpg", "jpeg", "heic", "png"}

#: Video extensions, added to a scan only when ``--include-video`` is given.
#: Off by default so an existing run's shape does not change under anyone.
DEFAULT_VIDEO_EXTENSIONS = {"mp4", "mov", "m4v"}


def partition_media(paths: list[Path], video_extensions: set[str] | None = None) -> tuple:
    """Split a scan into ``(images, videos)``, dropping Live Photo sidecars.

    Apple writes ``IMG_1234.HEIC`` and ``IMG_1234.MOV`` for a single capture.
    Both would otherwise be tagged, putting one moment in the library twice --
    as a photo and as a three-second clip of it -- and doubling the model calls
    for nothing. The clip is dropped when a still with the same stem is in the
    same scan.
    """
    from pyimgtag.video import is_live_photo_sidecar

    exts = video_extensions or DEFAULT_VIDEO_EXTENSIONS
    images = [p for p in paths if p.suffix.lstrip(".").lower() not in exts]
    still_stems = {p.stem.lower() for p in images}
    videos = [
        p
        for p in paths
        if p.suffix.lstrip(".").lower() in exts and not is_live_photo_sidecar(p, still_stems)
    ]
    return images, videos


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

    Returns:
        Sorted list of matching image file paths.

    Raises:
        FileNotFoundError: Path is not an existing directory.
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
