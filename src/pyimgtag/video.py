"""Video support: keyframe extraction via ffmpeg and per-clip aggregation.

A modern photo library is 20-40% video, and until now every one of those
files was invisible to ``query``, ``judge``, ``events`` and search. This module
makes a clip look like an image to the rest of the pipeline: sample a few
frames, let the existing vision path tag each one, then fold the frame results
into a single answer for the clip.

**ffmpeg is an external tool, exactly like exiftool.** Preferred when present,
reported clearly when absent, never a Python dependency -- decoding video in
process would be a far larger surface than this feature is worth.

Two halves live here, split so the interesting one needs no ffmpeg at all:

- :func:`extract_frames` and :func:`probe` shell out.
- :func:`aggregate_frames` is a pure function over
  :class:`~pyimgtag.models.TagResult` values, which is where the judgement
  calls are -- and so is where the tests are.
"""

from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404 — fixed argv, never a shell string
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyimgtag.models import TagResult

#: Extensions treated as video when ``--include-video`` is given.
DEFAULT_VIDEO_EXTENSIONS = {"mp4", "mov", "m4v"}

#: How many frames to sample per clip. Three -- near the start, the middle and
#: near the end -- catches a clip that changes subject without multiplying the
#: model cost the way a dense sample would.
DEFAULT_FRAME_COUNT = 3

#: Sample positions as a fraction of duration. Deliberately inside the clip:
#: the first and last frames of a phone video are very often a blur or a lens
#: cap, and tagging those would describe the pocket rather than the holiday.
_SAMPLE_FRACTIONS = (0.1, 0.5, 0.9)

#: How many tags survive aggregation, matching what the model returns per image.
_MAX_TAGS = 5

#: Most conservative first. A clip is only 'delete' when every frame agreed.
_CLEANUP_ORDER = ("keep", "review", "delete")

_TIMEOUT_SECONDS = 120


class VideoToolError(RuntimeError):
    """ffmpeg or ffprobe is missing, or failed on a specific file."""


@dataclass
class VideoInfo:
    """What ffprobe knows about a clip."""

    duration_sec: float | None = None
    codec: str | None = None
    width: int | None = None
    height: int | None = None
    creation_time: str | None = None


def ffmpeg_available() -> bool:
    """True when both ffmpeg and ffprobe are on PATH."""
    return bool(shutil.which("ffmpeg")) and bool(shutil.which("ffprobe"))


def _run(argv: list[str]) -> subprocess.CompletedProcess:
    """Run a fixed argument vector, never a shell string.

    Output is captured as bytes and decoded as UTF-8 here rather than through
    ``text=True``, which decodes with the locale encoding. On Windows that is
    cp1252, and ffprobe's JSON carries the filename: a path containing ``Ł``
    puts byte 0x81 on stdout, which cp1252 has no mapping for. The decode then
    raises inside subprocess's reader thread, where it cannot be caught -- it
    surfaces as an unraisable exception and the output is simply gone, so
    probe() reports no duration and extract_frames() returns fewer frames than
    it was asked for, both without an error.

    errors="replace" because ffmpeg's stderr is diagnostics rather than data,
    and one unmappable character in a log line must not end a run.
    """
    proc = subprocess.run(  # noqa: S603  # nosec B603
        argv, capture_output=True, timeout=_TIMEOUT_SECONDS, check=False
    )
    return subprocess.CompletedProcess(
        proc.args,
        proc.returncode,
        proc.stdout.decode("utf-8", "replace"),
        proc.stderr.decode("utf-8", "replace"),
    )


def probe(file_path: str | Path) -> VideoInfo:
    """Read duration, codec and dimensions from a clip.

    Raises:
        VideoToolError: ffprobe is missing or could not read the file.
    """
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise VideoToolError("ffprobe is not installed")

    proc = _run(
        [
            ffprobe,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            "-select_streams",
            "v:0",
            str(file_path),
        ]
    )
    if proc.returncode != 0:
        raise VideoToolError(f"ffprobe failed: {proc.stderr.strip()[:200]}")
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise VideoToolError(f"ffprobe returned unreadable JSON: {exc}") from exc

    fmt = payload.get("format") or {}
    streams = payload.get("streams") or [{}]
    stream = streams[0] if streams else {}

    duration = fmt.get("duration") or stream.get("duration")
    try:
        duration_sec = float(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration_sec = None

    tags = fmt.get("tags") or {}
    return VideoInfo(
        duration_sec=duration_sec,
        codec=stream.get("codec_name"),
        width=stream.get("width"),
        height=stream.get("height"),
        # QuickTime/MP4 keeps the capture time here, which is what makes a
        # video show up correctly in the timeline and the date filters.
        creation_time=tags.get("creation_time") or tags.get("com.apple.quicktime.creationdate"),
    )


def sample_positions(duration_sec: float | None, count: int) -> list[float]:
    """Timestamps to sample, in seconds.

    A clip with no readable duration still yields one position -- the opening
    frame -- rather than nothing: a tagged first frame beats an untagged clip.
    """
    count = max(1, count)
    if not duration_sec or duration_sec <= 0:
        return [0.0]
    if count == 1:
        return [duration_sec * 0.5]
    if count == len(_SAMPLE_FRACTIONS):
        return [duration_sec * f for f in _SAMPLE_FRACTIONS]
    # Evenly spaced strictly inside the clip.
    step = 1.0 / (count + 1)
    return [duration_sec * step * (i + 1) for i in range(count)]


def extract_frames(
    file_path: str | Path, count: int = DEFAULT_FRAME_COUNT, dest_dir: Path | None = None
) -> list[Path]:
    """Write *count* sampled JPEG frames and return their paths.

    The caller owns the files. When *dest_dir* is None a temporary directory is
    created and its path is the parent of the returned frames, so a caller that
    wants cleanup can remove it.

    Raises:
        VideoToolError: ffmpeg is missing, or no frame could be decoded.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise VideoToolError("ffmpeg is not installed")

    info = probe(file_path)
    directory = dest_dir or Path(tempfile.mkdtemp(prefix="pyimgtag-video-"))
    directory.mkdir(parents=True, exist_ok=True)

    frames: list[Path] = []
    for index, position in enumerate(sample_positions(info.duration_sec, count)):
        out = directory / f"frame{index:02d}.jpg"
        proc = _run(
            [
                ffmpeg,
                "-nostdin",
                "-loglevel",
                "error",
                # -ss before -i seeks by keyframe, which is fast and is why
                # this stays cheap on a long clip.
                "-ss",
                f"{position:.3f}",
                "-i",
                str(file_path),
                "-frames:v",
                "1",
                "-q:v",
                "3",
                "-y",
                str(out),
            ]
        )
        if proc.returncode == 0 and out.exists() and out.stat().st_size > 0:
            frames.append(out)

    if not frames:
        raise VideoToolError(f"ffmpeg decoded no frames from {Path(file_path).name}")
    return frames


def aggregate_frames(results: list[TagResult]) -> TagResult:
    """Fold per-frame tag results into one answer for the clip.

    The rules, and why:

    - **Tags** are the union, ordered by how many frames agreed. A subject that
      appears throughout the clip should outrank one that flashes past.
    - **Scene, tone, event hint, significance** take the majority. These are
      single-valued, and the most common reading across the clip is the
      clip's.
    - **cleanup_class** takes the *most conservative* verdict, not the
      majority. A clip is only ``delete`` when every frame said so -- one good
      frame is enough reason to keep a video, and the cost of being wrong here
      is somebody's memory.
    - **has_text** is true when any frame had text, since a title card is text
      in the clip whether or not it fills it.
    - **Summary** comes from the middle frame when there is one, which is the
      frame most likely to show what the clip is about.

    A list containing only errored frames returns an errored result naming how
    many failed, rather than an empty success.
    """
    from pyimgtag.models import TagResult as _TagResult

    usable = [r for r in results if not r.error]
    if not usable:
        return _TagResult(error=f"all {len(results)} sampled frame(s) failed")

    tag_counts: Counter[str] = Counter()
    for result in usable:
        # Counted once per frame, so a tag repeated within one frame does not
        # outvote a tag present in every frame.
        for tag in dict.fromkeys(result.tags):
            tag_counts[tag] += 1
    tags = [tag for tag, _ in sorted(tag_counts.items(), key=lambda kv: (-kv[1], kv[0]))][
        :_MAX_TAGS
    ]

    def _majority(field: str) -> str | None:
        values = [getattr(r, field) for r in usable if getattr(r, field)]
        if not values:
            return None
        counts = Counter(values)
        top = max(counts.values())
        # Ties break alphabetically so the answer does not depend on frame order.
        return sorted(v for v, c in counts.items() if c == top)[0]

    cleanups = [r.cleanup_class for r in usable if r.cleanup_class]
    cleanup = None
    if cleanups:
        cleanup = min(
            cleanups,
            key=lambda c: _CLEANUP_ORDER.index(c) if c in _CLEANUP_ORDER else len(_CLEANUP_ORDER),
        )

    middle = usable[len(usable) // 2]
    text_frames = [r.text_summary for r in usable if r.has_text and r.text_summary]

    return _TagResult(
        tags=tags,
        summary=middle.summary,
        scene_category=_majority("scene_category"),
        emotional_tone=_majority("emotional_tone"),
        cleanup_class=cleanup,
        has_text=any(r.has_text for r in usable),
        text_summary=text_frames[0] if text_frames else None,
        event_hint=_majority("event_hint"),
        significance=_majority("significance"),
    )


def is_live_photo_sidecar(video_path: Path, siblings: set[str]) -> bool:
    """True when this clip is the motion half of a Live Photo already covered.

    Apple writes ``IMG_1234.HEIC`` and ``IMG_1234.MOV`` for one capture. Tagging
    both puts the same moment in the library twice -- once as a photo and once
    as a three-second clip of it -- which is noise in every grid and doubles
    the model calls for nothing.

    *siblings* is the set of lowercase stems of the still images being
    processed alongside it.
    """
    return video_path.stem.lower() in siblings
