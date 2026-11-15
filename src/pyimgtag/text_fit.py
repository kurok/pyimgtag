"""Fit text into fixed-width terminal table columns, marking every cut with "…"."""

from __future__ import annotations

import unicodedata

_ELLIPSIS = "…"
_WORD_BREAKS = " _-"


def _first_of(text: str, chars: str) -> int | None:
    """Index of the first character of *text* that is in *chars*, or None."""
    found = [i for i in (text.find(c) for c in chars) if i >= 0]
    return min(found) if found else None


def shorten(text: str, width: int) -> str:
    """Cut *text* to *width* characters on a word boundary, marked with "…".

    Args:
        text: The text to fit.
        width: The column width, in characters.

    Returns:
        *text* unchanged when it fits, else its start plus ``"…"``.
    """
    if len(text) <= width:
        return text
    if width <= 1:
        return _ELLIPSIS[:width]
    cut = text[: width - 1]
    if text[width - 1] != " ":
        # The cut falls inside a word: back up to the previous space.
        space = cut.rfind(" ")
        if space >= width // 2:
            cut = cut[:space]
    return cut.rstrip(" ,;:—-") + _ELLIPSIS


def elide_path(path: str, width: int) -> str:
    """Fit *path* into *width* characters by dropping its start, marked with "…".

    The end of a path (the folder and file name) is the part that tells rows
    apart, so it is the part kept. The cut moves forward to the next path
    separator, so the shown text begins with a whole folder name
    (``…/Phone Import 2024/IMG_1.jpg``). When that would leave less than half
    the column (one long folder name just past the cut), the cut moves to the
    next word in that folder name instead, so the folder is not lost. Only a
    single word longer than half the column is cut inside the word.

    Args:
        path: The full path.
        width: The column width, in characters.

    Returns:
        *path* unchanged when it fits, else ``"…"`` plus the kept tail.
    """
    if len(path) <= width:
        return path
    if width <= 1:
        return _ELLIPSIS[:width]
    tail = path[-(width - 1) :]
    keep_at_least = (width - 1) // 2
    sep = _first_of(tail, "/\\")
    if sep is not None and len(tail) - sep >= keep_at_least:
        tail = tail[sep:]
    elif path[-width] not in _WORD_BREAKS + "/\\":
        # No usable separator: start at the next word instead of mid-word.
        brk = _first_of(tail, _WORD_BREAKS)
        if brk is not None and len(tail) - brk - 1 >= keep_at_least:
            tail = tail[brk + 1 :]
    # macOS hands back decomposed (NFD) names: never start on a combining mark,
    # which would render on the ellipsis instead of on its letter.
    while tail and unicodedata.combining(tail[0]):
        tail = tail[1:]
    return _ELLIPSIS + tail
