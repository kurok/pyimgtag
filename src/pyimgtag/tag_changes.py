"""Validation shared by the tag rename / merge / delete paths (CLI and web API).

A typo in a tag name used to look like success ("renamed in 0 images"). These
checks run before any write, against the tag counts the database already
reports, using the same lower-casing the database applies to tags.
"""

from __future__ import annotations

from collections.abc import Iterable


class TagChangeError(ValueError):
    """A tag change that would do nothing or name a tag that does not exist.

    Attributes:
        kind: ``"not_found"`` when the source tag is not in the library, or
            ``"invalid"`` when the request itself is a no-op (empty name, or
            source and target are the same tag).
    """

    def __init__(self, kind: str, message: str) -> None:
        self.kind = kind
        super().__init__(message)


def _normalise(tag: str) -> str:
    return tag.strip().lower()


def check_tag_change(
    tag_counts: Iterable[tuple[str, int]], source: str, target: str | None = None
) -> None:
    """Raise :class:`TagChangeError` unless renaming/merging/deleting *source* is real.

    Args:
        tag_counts: ``(tag, count)`` pairs as returned by ``get_tag_counts()``.
        source: The tag being renamed, merged or deleted.
        target: The new name (rename) or merge target; ``None`` for delete.

    Raises:
        TagChangeError: ``kind="invalid"`` for an empty name or a target equal
            to the source; ``kind="not_found"`` when *source* has no images.
            Request problems are reported before the lookup.
    """
    src = _normalise(source)
    if not src:
        raise TagChangeError("invalid", "tag name is empty")
    if target is not None:
        tgt = _normalise(target)
        if not tgt:
            raise TagChangeError("invalid", "new tag name is empty")
        if tgt == src:
            raise TagChangeError("invalid", "source and target are the same tag")
    if src not in {_normalise(tag) for tag, _ in tag_counts}:
        raise TagChangeError("not_found", f"no tag '{source}'")
