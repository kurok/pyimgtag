#!/usr/bin/env python3
"""
Capture real pyimgtag output against mock Ollama for wiki documentation.

Usage:
    cd /path/to/pyimgtag
    python3 examples/capture_demo.py

Outputs: examples/captured/<command>.txt for each captured command.
Requires: pyimgtag installed (pip install -e ".[dev]")
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
FIXTURES_DIR = REPO_ROOT / "examples" / "fixtures"
CAPTURED_DIR = REPO_ROOT / "examples" / "captured"
MOCK_PORT = 11435
MOCK_URL = f"http://127.0.0.1:{MOCK_PORT}"
DEMO_DB = "/tmp/pyimgtag-demo.db"


def run(cmd: list[str], cwd: Path | None = None) -> str:
    """Run a command and return combined stdout+stderr as a string."""
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(cwd or REPO_ROOT),
    )
    return (result.stdout + result.stderr).strip()


def save(name: str, header: str, output: str) -> None:
    CAPTURED_DIR.mkdir(parents=True, exist_ok=True)
    path = CAPTURED_DIR / f"{name}.txt"
    path.write_text(f"$ {header}\n{output}\n")
    print(f"  saved {path.name}")


def main() -> None:
    # Remove stale demo DB
    Path(DEMO_DB).unlink(missing_ok=True)

    # Start mock Ollama server
    mock_proc = subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "examples" / "mock_ollama.py"), str(MOCK_PORT)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    time.sleep(0.5)
    print(f"Mock Ollama started on {MOCK_URL}")

    fixtures = str(FIXTURES_DIR)
    pyimgtag = [sys.executable, "-m", "pyimgtag"]
    # Subcommand-scoped flags: --ollama-url and --db go after the subcommand name
    run_flags = ["--ollama-url", MOCK_URL, "--db", DEMO_DB]
    db_flag = ["--db", DEMO_DB]

    try:
        # 1. preflight
        save("preflight",
             f"pyimgtag preflight --input-dir examples/fixtures --ollama-url {MOCK_URL}",
             run([*pyimgtag, "preflight",
                  "--input-dir", fixtures, "--ollama-url", MOCK_URL]))

        # 2. run dry-run (first 3 images, verbose)
        save("run_dry_run",
             "pyimgtag run --input-dir examples/fixtures --limit 3 --dry-run --verbose",
             run([*pyimgtag, "run",
                  "--input-dir", fixtures, "--limit", "3", "--dry-run", "--verbose",
                  *run_flags]))

        # 3. run real (all 6 fixtures, seeds DB)
        save("run_real",
             "pyimgtag run --input-dir examples/fixtures",
             run([*pyimgtag, "run", "--input-dir", fixtures, *run_flags]))

        # 4. status
        save("status",
             "pyimgtag status",
             run([*pyimgtag, "status", *db_flag]))

        # 5. cleanup
        save("cleanup",
             "pyimgtag cleanup",
             run([*pyimgtag, "cleanup", *db_flag]))

        # 6. cleanup --include-review
        save("cleanup_review",
             "pyimgtag cleanup --include-review",
             run([*pyimgtag, "cleanup", "--include-review", *db_flag]))

        # 7. query table
        save("query_table",
             "pyimgtag query --format table",
             run([*pyimgtag, "query", "--format", "table", *db_flag]))

        # 8. query with filters
        save("query_filters",
             "pyimgtag query --cleanup delete --format table",
             run([*pyimgtag, "query", "--cleanup", "delete", "--format", "table", *db_flag]))

        # 9. tags list
        save("tags_list",
             "pyimgtag tags list",
             run([*pyimgtag, "tags", "list", *db_flag]))

        # 10. tags rename
        save("tags_rename",
             "pyimgtag tags rename golden-hour golden_hour",
             run([*pyimgtag, "tags", "rename", "golden-hour", "golden_hour", *db_flag]))

        # 11. tags list after rename
        save("tags_list_after_rename",
             "pyimgtag tags list  # after rename",
             run([*pyimgtag, "tags", "list", *db_flag]))

        # 12. reprocess --status error
        save("reprocess",
             "pyimgtag reprocess --status error",
             run([*pyimgtag, "reprocess", "--status", "error", *db_flag]))

        # 13. run with --output-json
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            json_path = f.name
        Path(DEMO_DB).unlink(missing_ok=True)
        run([*pyimgtag, "run", "--input-dir", fixtures, "--output-json", json_path, *run_flags])
        try:
            data = json.loads(Path(json_path).read_text())
            first = data[0] if data else {}
        except Exception:
            first = {}
        save("output_json",
             "pyimgtag run --input-dir examples/fixtures --output-json results.json",
             f"# results.json (first entry)\n{json.dumps(first, indent=2)}")
        Path(json_path).unlink(missing_ok=True)

        # 14. run with dedup
        Path(DEMO_DB).unlink(missing_ok=True)
        save("run_dedup",
             "pyimgtag run --input-dir examples/fixtures --dedup",
             run([*pyimgtag, "run", "--input-dir", fixtures, "--dedup", *run_flags]))

        # 15. faces scan (real output — likely 0 faces with solid-color fixtures)
        save("faces_scan",
             "pyimgtag faces scan --input-dir examples/fixtures",
             run([*pyimgtag, "faces", "scan",
                  "--input-dir", fixtures, *db_flag]))

    finally:
        mock_proc.terminate()
        mock_proc.wait()
        print("Mock Ollama stopped.")

    print(f"\nAll captures saved to {CAPTURED_DIR}")


if __name__ == "__main__":
    main()
