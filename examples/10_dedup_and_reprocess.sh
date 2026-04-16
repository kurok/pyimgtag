#!/usr/bin/env bash
# Wiki: https://github.com/kurok/pyimgtag/wiki/Managing-Your-Library
# Skip duplicate images via perceptual hash, then reprocess after prompt changes.
set -euo pipefail

INPUT_DIR="${1:-~/Pictures/exported}"

echo "=== run with perceptual deduplication ==="
pyimgtag run \
  --input-dir "$INPUT_DIR" \
  --dedup \
  --dedup-threshold 5 \
  --verbose

echo ""
echo "=== check current progress ==="
pyimgtag status

echo ""
echo "=== reprocess only failed entries ==="
pyimgtag reprocess --status error

echo ""
echo "=== reprocess ALL (e.g. after improving prompt/model) ==="
echo "# Uncomment the line below to reset everything:"
echo "# pyimgtag reprocess"
