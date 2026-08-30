#!/usr/bin/env bash
# Wiki: https://github.com/kurok/pyimgtag/wiki/Advanced-Topics
# Controlled vocabulary, custom prompt template, and output language.
set -euo pipefail

PHOTOS_DIR="${1:-~/Pictures/birds}"
OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== pyimgtag controlled vocabulary examples ==="
echo "Photos dir: $PHOTOS_DIR"
echo ""

# --- Example 1: Tag with a vocabulary (JSON needs no extra deps) ---
echo "-- 1. Tag with the birding vocabulary; off-vocab tags are kept and counted --"
pyimgtag run \
  --input-dir "$PHOTOS_DIR" \
  --vocabulary "$HERE/vocabularies/birding.json" \
  --limit 20 \
  --output-json birds.json \
  --ollama-url "$OLLAMA_URL"
echo "Mapping report: birds.vocabulary.json (raw -> canonical counts)"
echo ""

# --- Example 2: Hierarchy roll-up in query ---
echo "-- 2. Everything under 'bird' (raptor, waterfowl, songbird, seabird) --"
pyimgtag query \
  --tag bird --include-children \
  --vocabulary "$HERE/vocabularies/birding.json"
echo ""

# --- Example 3: Custom prompt template ---
echo "-- 3. Start from the default template, edit the domain wording, re-run --"
pyimgtag prompt show > my-prompt.txt
sed -i.bak 's/Tag this image for a photo gallery./Tag this bird photo for a field guide./' my-prompt.txt
pyimgtag run \
  --input-dir "$PHOTOS_DIR" \
  --prompt-template my-prompt.txt \
  --vocabulary "$HERE/vocabularies/birding.json" \
  --limit 5 \
  --ollama-url "$OLLAMA_URL"
echo ""

# --- Example 4: Output language (model-dependent) ---
echo "-- 4. Portuguese summaries, vocabulary tags stay canonical --"
PYIMGTAG_VOCABULARY="$HERE/vocabularies/birding.json" pyimgtag run \
  --input-dir "$PHOTOS_DIR" \
  --tag-language Portuguese \
  --limit 5 \
  --ollama-url "$OLLAMA_URL"
