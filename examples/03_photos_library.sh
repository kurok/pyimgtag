#!/usr/bin/env bash
# Wiki: https://github.com/kurok/pyimgtag/wiki/Tagging-Your-Photos
# Scan Apple Photos library and write tags + descriptions back (macOS only).
set -euo pipefail

if [[ "$(uname)" != "Darwin" ]]; then
  echo "Apple Photos integration is macOS-only. Use 01_basic_run.sh on Linux/Windows."
  exit 1
fi

PHOTOS_LIB="${1:-$HOME/Pictures/Photos Library.photoslibrary}"

echo "=== Apple Photos library scan + write-back ==="
echo "Library: $PHOTOS_LIB"
echo ""

# --write-back pushes AI tags and descriptions back as Photos keywords/captions
pyimgtag run \
  --photos-library "$PHOTOS_LIB" \
  --write-back \
  --limit 50 \
  --verbose

echo ""
echo "Tags and descriptions written back to Apple Photos."
