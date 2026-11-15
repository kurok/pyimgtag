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
        width: The column width, in terminal cells.

    Returns:
        *path* unchanged when it fits, else ``"…"`` plus the kept tail.
    """
    if display_width(path) <= width:
        return path
    if width <= 1:
        return _ELLIPSIS[:width]
    tail = _tail_fitting(path, width - 1)
    cut_at = path[len(path) - len(tail) - 1]
    keep_at_least = (width - 1) // 2
    sep = _first_of(tail, "/\\")
    if sep is not None and display_width(tail[sep:]) >= keep_at_least:
        tail = tail[sep:]
    elif cut_at not in _WORD_BREAKS + "/\\":
        # No usable separator: start at the next word instead of mid-word.
        brk = _first_of(tail, _WORD_BREAKS)
        if brk is not None and display_width(tail[brk + 1 :]) >= keep_at_least:
            tail = tail[brk + 1 :]
    # macOS hands back decomposed (NFD) names: never start on a combining mark,
    # which would render on the ellipsis instead of on its letter.
    while tail and unicodedata.combining(tail[0]):
        tail = tail[1:]
    return _ELLIPSIS + tail


def display_width(text: str) -> int:
    """Terminal columns *text* takes: combining marks 0, wide (CJK) characters 2.

    Args:
        text: The text to measure.

    Returns:
        The number of terminal cells.
    """
    return sum(_char_width(c) for c in text)


def pad(text: str, width: int) -> str:
    """Left-align *text* in *width* terminal columns, counting display width.

    Args:
        text: The text to pad.
        width: The column width, in terminal cells.

    Returns:
        *text* followed by enough spaces to fill the column.
    """
    return text + " " * max(0, width - display_width(text))


def _char_width(char: str) -> int:
    if unicodedata.combining(char):
        return 0
    return 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1


def _tail_fitting(text: str, width: int) -> str:
    """The longest end of *text* that fits in *width* terminal cells."""
    used = 0
    start = len(text)
    while start > 0:
        w = _char_width(text[start - 1])
        if used + w > width:
            break
        used += w
        start -= 1
    return text[start:]
