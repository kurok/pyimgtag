"""Handler for the ``judge`` subcommand — photo quality scoring."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pyimgtag.applescript_writer import write_to_photos
from pyimgtag.judge_scorer import compute_scores, strongest, weakest
from pyimgtag.models import JudgeResult, JudgeScores
from pyimgtag.ollama_client import OllamaClient
from pyimgtag.preflight import check_ollama
from pyimgtag.scanner import scan_directory, scan_photos_library

if TYPE_CHECKING:
    pass


def _score_label(score: float) -> str:
    if score >= 4.5:
        return "outstanding"
    if score >= 4.0:
        return "strong"
    if score >= 3.5:
        return "solid"
    if score >= 3.0:
        return "acceptable"
    return "weak"


def _print_brief(result: JudgeResult, idx: int, total: int) -> None:
    top = strongest(result.scores, 2)
    bot = weakest(result.scores, 2)
    label = _score_label(result.weighted_score)
    print(
        f"[{idx}/{total}] {result.file_name} → "
        f"{result.weighted_score:.2f}/5 {label} | "
        f"+ {', '.join(top)} | - {', '.join(bot)}"
    )
    if result.scores.verdict:
        print(f"  {result.scores.verdict}")


def _print_verbose(result: JudgeResult, idx: int, total: int) -> None:
    print(f"[{idx}/{total}] {result.file_name}")
    print(
        f"  Score:   {result.weighted_score:.2f}/5  "
        f"(core: {result.core_score:.2f}, visible: {result.visible_score:.2f})"
    )
    top = strongest(result.scores, 3)
    bot = weakest(result.scores, 3)
    print(f"  Best:    {', '.join(f'{k}={getattr(result.scores, k):.0f}' for k in top)}")
    print(f"  Weakest: {', '.join(f'{k}={getattr(result.scores, k):.0f}' for k in bot)}")
    if result.scores.verdict:
        print(f"  Verdict: {result.scores.verdict}")


def _result_to_dict(result: JudgeResult) -> dict[str, Any]:
    scores = result.scores
    return {
        "file_path": result.file_path,
        "file_name": result.file_name,
        "weighted_score": round(result.weighted_score, 4),
        "core_score": round(result.core_score, 4),
        "visible_score": round(result.visible_score, 4),
        "verdict": scores.verdict,
        "scores": {
            "impact": scores.impact,
            "story_subject": scores.story_subject,
            "composition_center": scores.composition_center,
            "lighting": scores.lighting,
            "creativity_style": scores.creativity_style,
            "color_mood": scores.color_mood,
            "presentation_crop": scores.presentation_crop,
            "technical_excellence": scores.technical_excellence,
            "focus_sharpness": scores.focus_sharpness,
            "exposure_tonal": scores.exposure_tonal,
            "noise_cleanliness": scores.noise_cleanliness,
            "subject_separation": scores.subject_separation,
            "edit_integrity": scores.edit_integrity,
        },
    }


def cmd_judge(args: argparse.Namespace, _db: Any) -> int:
    ok, msg = check_ollama(args.ollama_url)
    if not ok:
        print(f"Ollama not available: {msg}", file=sys.stderr)
        return 1

    exts = {e.lstrip(".") for e in args.extensions.split(",")}

    if not getattr(args, "photos_library", None) and not getattr(args, "input_dir", None):
        print("Error: one of --input-dir or --photos-library is required", file=sys.stderr)
        return 1

    write_back = getattr(args, "write_back", False)
    write_back_mode = getattr(args, "write_back_mode", "overwrite")

    if getattr(args, "photos_library", None):
        try:
            files = scan_photos_library(
                args.photos_library,
                extensions=exts,
            )
        except (PermissionError, FileNotFoundError) as exc:
            print(f"Error scanning Photos library: {exc}", file=sys.stderr)
            return 1
    else:
        try:
            files = scan_directory(
                args.input_dir,
                extensions=exts,
                recursive=not getattr(args, "no_recursive", False),
            )
        except (PermissionError, FileNotFoundError) as exc:
            print(f"Error scanning directory: {exc}", file=sys.stderr)
            return 1

    if not files:
        print("No image files found.", file=sys.stderr)
        return 0

    if args.limit:
        files = files[: args.limit]

    ollama = OllamaClient(
        model=args.model,
        base_url=args.ollama_url,
        max_dim=args.max_dim,
        timeout=args.timeout,
    )

    results: list[JudgeResult] = []
    total = len(files)

    for idx, file_path in enumerate(files, start=1):
        scores: JudgeScores | None = ollama.judge_image(str(file_path))
        if scores is None:
            print(f"  [{idx}/{total}] {file_path.name}: judge failed, skipping", file=sys.stderr)
            continue

        weighted, core, visible = compute_scores(scores)
        result = JudgeResult(
            file_path=str(file_path),
            file_name=file_path.name,
            scores=scores,
            weighted_score=weighted,
            core_score=core,
            visible_score=visible,
        )

        if args.min_score is not None and weighted < args.min_score:
            continue

        results.append(result)

        if _db is not None:
            _db.save_judge_result(result)

        if write_back and getattr(args, "photos_library", None):
            score_tag = f"score:{weighted:.1f}"
            err = write_to_photos(
                result.file_name,
                [score_tag],
                None,
                mode=write_back_mode,
            )
            if err:
                print(f"  Write-back failed: {err}", file=sys.stderr)

        if args.verbose:
            _print_verbose(result, idx, total)
        else:
            _print_brief(result, idx, total)

    if args.sort_by == "score":
        results.sort(key=lambda r: r.weighted_score, reverse=True)
    else:
        results.sort(key=lambda r: r.file_name)

    if args.output_json:
        Path(args.output_json).write_text(
            json.dumps([_result_to_dict(r) for r in results], indent=2),
            encoding="utf-8",
        )

    return 0
