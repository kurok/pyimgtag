#!/usr/bin/env bash
# Wiki: https://github.com/kurok/pyimgtag/wiki/Face-Recognition
# Full face recognition pipeline: scan → cluster → review → apply keywords.
set -euo pipefail

INPUT_DIR="${1:-~/Pictures/exported}"

echo "=== step 1: detect faces and compute embeddings ==="
pyimgtag faces scan --input-dir "$INPUT_DIR" --verbose

echo ""
echo "=== step 2: cluster faces into person groups ==="
pyimgtag faces cluster --eps 0.5 --min-samples 2

echo ""
echo "=== step 3: review detected persons ==="
pyimgtag faces review

echo ""
echo "=== step 4: write person keywords to image EXIF ==="
pyimgtag faces apply --write-exif

echo ""
echo "Done. Persons are now stored as EXIF keywords."
