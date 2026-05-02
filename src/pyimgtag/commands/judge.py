"""Handler for the ``judge`` subcommand — photo quality scoring."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pyimgtag import run_registry
from pyimgtag.applescript_writer import write_to_photos
from pyimgtag.judge_scorer import compute_scores, strongest, weakest
from pyimgtag.models import JudgeResult, JudgeScores
from pyimgtag.ollama_client import OllamaClient
from pyimgtag.preflight import check_ollama
from pyimgtag.scanner import scan_directory, scan_photos_library
from pyimgtag.webapp.bootstrap import start_dashboard_for

if TYPE_CHECKING:
    pass


def _score_label(score: int) -> str:
    if score >= 9:
        return "outstanding"
    if score >= 8:
        return "strong"
    if score >= 7:
        return "solid"
    if score >= 5:
        return "acceptable"
    return "weak"


def _print_brief(result: JudgeResult, idx: int, total: int) -> None:
    top = strongest(result.scores, 2)
    bot = weakest(result.scores, 2)
    label = _score_label(result.weighted_score)
    print(
        f"[{idx}/{total}] {result.file_name} → "
        f"{result.weighted_score}/10 {label} | "
        f"+ {', '.join(top)} | - {', '.join(bot)}"
    )
    if result.scores.verdict:
        print(f"  {result.scores.verdict}")


def _print_verbose(result: JudgeResult, idx: int, total: int) -> None:
    print(f"[{idx}/{total}] {result.file_name}")
    print(
        f"  Score:   {result.weighted_score}/10  "
        f"(core: {result.core_score}, visible: {result.visible_score})"
    )
    top = strongest(result.scores, 3)
    bot = weakest(result.scores, 3)
    print(f"  Best:    {', '.join(f'{k}={getattr(result.scores, k)}' for k in top)}")
    print(f"  Weakest: {', '.join(f'{k}={getattr(result.scores, k)}' for k in bot)}")
    if result.scores.verdict:
        print(f"  Verdict: {result.scores.verdict}")


def _result_to_dict(result: JudgeResult) -> dict[str, Any]:
    scores = result.scores
    return {
        "file_path": result.file_path,
        "file_name": result.file_name,
        "weighted_score": result.weighted_score,
        "core_score": result.core_score,
        "visible_score": result.visible_score,
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

    exts = {e.strip().lstrip(".").lower() for e in args.extensions.split(",") if e.strip()}

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

    session, dashboard = start_dashboard_for(args, command="judge")
    if session is not None:
        session.set_counter("scanned", total)
        session.mark_running()

    try:
        try:
            for idx, file_path in enumerate(files, start=1):
                if session is not None:
                    session.wait_if_paused()
                    session.set_current(str(file_path))

                scores: JudgeScores | None = ollama.judge_image(str(file_path))
                if scores is None:
                    print(
                        f"  [{idx}/{total}] {file_path.name}: judge failed, skipping",
                        file=sys.stderr,
                    )
                    if session is not None:
                        session.increment("judge_failed")
                        session.record_item(str(file_path), "error", error="judge failed")
                        session.set_current(None)
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
                    if session is not None:
                        session.increment("skipped_min_score")
                        session.set_current(None)
                    continue

                results.append(result)

                if _db is not None:
                    _db.save_judge_result(result)

                if write_back and getattr(args, "photos_library", None):
                    score_tag = f"score:{weighted}"
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

                if session is not None:
                    session.record_item(str(file_path), "ok")
                    session.increment("processed")
                    session.set_current(None)
        except KeyboardInterrupt:
            if session is not None:
                session.mark_interrupted()
            print("\nInterrupted.", file=sys.stderr)

        if args.sort_by == "score":
            results.sort(key=lambda r: r.weighted_score, reverse=True)
        else:
            results.sort(key=lambda r: r.file_name)

        if args.output_json:
            Path(args.output_json).write_text(
                json.dumps([_result_to_dict(r) for r in results], indent=2),
                encoding="utf-8",
            )

        if session is not None:
            session.mark_completed()
    finally:
        if dashboard is not None:
            dashboard.stop()
        run_registry.set_current(None)

    return 0
