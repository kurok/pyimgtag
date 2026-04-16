#!/usr/bin/env bash
# Wiki: https://github.com/kurok/pyimgtag/wiki/Tagging-Your-Photos
# Basic dry-run on an exported folder — safe to run, writes nothing.
set -euo pipefail

INPUT_DIR="${1:-~/Pictures/exported}"

echo "=== pyimgtag basic dry-run ==="
echo "Input: $INPUT_DIR"
echo ""

# Dry-run: shows verbose output per image, no DB writes
pyimgtag run \
  --input-dir "$INPUT_DIR" \
  --limit 20 \
  --dry-run \
  --verbose

echo ""
echo "Tip: remove --dry-run to write results to the progress DB."
