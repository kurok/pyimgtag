#!/usr/bin/env bash
# Wiki: https://github.com/kurok/pyimgtag/wiki/Scoring-Photos
# Score photos with the 13-criterion professional quality rubric.
set -euo pipefail

PHOTOS_DIR="${1:-~/Pictures/exported}"
OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"

echo "=== pyimgtag judge examples ==="
echo "Photos dir: $PHOTOS_DIR"
echo ""

# --- Example 1: Basic score run ---
echo "-- 1. Score first 10 images (brief output) --"
pyimgtag judge \
  --input-dir "$PHOTOS_DIR" \
  --limit 10 \
  --ollama-url "$OLLAMA_URL"

echo ""

# --- Example 2: Verbose per-criterion breakdown ---
echo "-- 2. Verbose breakdown (first 5 images) --"
pyimgtag judge \
  --input-dir "$PHOTOS_DIR" \
  --limit 5 \
  --verbose \
  --ollama-url "$OLLAMA_URL"

echo ""

# --- Example 3: Filter by minimum score ---
echo "-- 3. Only show photos scoring 4.0 or above --"
pyimgtag judge \
  --input-dir "$PHOTOS_DIR" \
  --limit 20 \
  --min-score 4.0 \
  --ollama-url "$OLLAMA_URL"

echo ""

# --- Example 4: Save ranked results to JSON ---
echo "-- 4. Save ranking to JSON --"
pyimgtag judge \
  --input-dir "$PHOTOS_DIR" \
  --limit 20 \
  --output-json /tmp/judge_ranking.json \
  --ollama-url "$OLLAMA_URL"

echo "Saved to /tmp/judge_ranking.json"
if command -v jq &>/dev/null; then
  echo "Top result:"
  jq '.[0] | {file_name, weighted_score, verdict}' /tmp/judge_ranking.json
fi

echo ""

# --- Example 5: Sort by name instead of score ---
echo "-- 5. Sort output alphabetically by filename --"
pyimgtag judge \
  --input-dir "$PHOTOS_DIR" \
  --limit 10 \
  --sort-by name \
  --ollama-url "$OLLAMA_URL"

echo ""

# --- Example 6: Photos library (macOS only) ---
if [[ "$(uname)" == "Darwin" ]]; then
  PHOTOS_LIB="${PHOTOS_LIB:-$HOME/Pictures/Photos Library.photoslibrary}"
  if [[ -d "$PHOTOS_LIB" ]]; then
    echo "-- 6. Score from Photos library (first 10, min score 3.5) --"
    pyimgtag judge \
      --photos-library "$PHOTOS_LIB" \
      --limit 10 \
      --min-score 3.5 \
      --output-json /tmp/judge_photos_lib.json \
      --ollama-url "$OLLAMA_URL"
    echo "Saved to /tmp/judge_photos_lib.json"
  else
    echo "-- 6. Photos library not found at $PHOTOS_LIB (skipping) --"
  fi
fi

echo ""
echo "=== Done ==="
