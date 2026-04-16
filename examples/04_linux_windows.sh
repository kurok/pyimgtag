#!/usr/bin/env bash
# Wiki: https://github.com/kurok/pyimgtag/wiki/Advanced-Topics
# Cross-platform: tag an exported folder and write results to EXIF + JSON.
# Works on macOS, Linux, and Windows (Git Bash / WSL).
set -euo pipefail

INPUT_DIR="${1:-/mnt/photos}"
JSON_OUT="${2:-results.json}"

echo "=== cross-platform tag run ==="
echo "Input: $INPUT_DIR"
echo ""

pyimgtag run \
  --input-dir "$INPUT_DIR" \
  --output-json "$JSON_OUT" \
  --write-exif \
  --verbose

echo ""
echo "Results in $JSON_OUT; EXIF updated (if exiftool is installed)."
