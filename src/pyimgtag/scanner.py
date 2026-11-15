"""File scanning for directories and Apple Photos library packages."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

#: The image types a scan picks up unless ``--extensions`` says otherwise. One
#: list for ``run``, ``judge`` and ``index`` so a library is read the same way
#: by all three.
DEFAULT_EXTENSIONS_ARG = "jpg,jpeg,heic,png,tif,tiff,webp"
DEFAULT_EXTENSIONS = set(DEFAULT_EXTENSIONS_ARG.split(","))

#: Video extensions, added to a scan only when ``--include-video`` is given.
#: Off by default so an existing run's shape does not change under anyone.
DEFAULT_VIDEO_EXTENSIONS = {"mp4", "mov", "m4v"}

#: Photo, RAW and clip types beyond the defaults. A file of one of these kinds
#: that a scan leaves out is reported (see ``unsupported``), so a library is not
#: half-tagged in silence. Anything else (``.xmp``, ``.aae``, ``.json``) is not
#: media and is never mentioned.
_OTHER_MEDIA_EXTENSIONS = {
    "heif",
    "gif",
    "bmp",
    "avif",
    "jxl",
    "avi",
    "mkv",
    "3gp",
    "mts",
    "m2ts",
    "webm",
}


def _is_media_extension(ext: str) -> bool:
    from pyimgtag.raw_converter import RAW_EXTENSIONS

    return (
        ext in DEFAULT_EXTENSIONS
        or ext in DEFAULT_VIDEO_EXTENSIONS
        or ext in _OTHER_MEDIA_EXTENSIONS
        or f".{ext}" in RAW_EXTENSIONS
    )


def _select(entries: Iterable[Path], exts: set[str], unsupported: list[Path] | None) -> list[Path]:
    """Sorted files of *entries* whose extension is in *exts*.

    When *unsupported* is a list, the media files left out because of their
    extension are appended to it, sorted.
    """
    files: list[Path] = []
    left_out: list[Path] = []
    for entry in entries:
        if not entry.is_file():
            continue
        ext = entry.suffix.lstrip(".").lower()
        if ext in exts:
            files.append(entry)
        elif unsupported is not None and not entry.name.startswith("."):
            if _is_media_extension(ext):
                left_out.append(entry)
    if unsupported is not None:
        unsupported.extend(sorted(left_out))
    return sorted(files)


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
    unsupported: list[Path] | None = None,
) -> list[Path]:
    """Scan a directory for image files, sorted by name.

    Args:
        path: Directory to scan.
        extensions: File extensions to include (without dots).
        recursive: When True (default), scan subdirectories recursively.
        unsupported: When a list is given, the photo/RAW/video files left out
            because their extension is not in *extensions* are appended to it.

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
    return _select(root.glob(pattern), exts, unsupported)


def scan_photos_library(
    library_path: str | Path,
    extensions: set[str] | None = None,
    unsupported: list[Path] | None = None,
) -> list[Path]:
    """Best-effort scan of originals inside an Apple Photos library package.

    Tries ``originals/`` first (modern format), then ``Masters/`` (older format).
    *unsupported* works as in :func:`scan_directory`.

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

    files = _select(originals.rglob("*"), exts, unsupported)

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
