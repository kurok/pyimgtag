#!/usr/bin/env bash
# Wiki: https://github.com/kurok/pyimgtag/wiki/Advanced-Topics
# Tag RAW and HEIC files — requires optional dependencies.
set -euo pipefail

INPUT_DIR="${1:-~/Pictures/raw}"

echo "=== optional dependency check ==="
echo "RAW support needs:  pip install pyimgtag[raw]"
echo "HEIC support needs: pip install pyimgtag[heic]  +  brew install exiftool"
echo ""

echo "=== tag RAW files (CR2, NEF, ARW, DNG) ==="
pyimgtag run \
  --input-dir "$INPUT_DIR" \
  --extensions cr2,nef,arw,dng \
  --verbose

echo ""
echo "=== tag HEIC files (iPhone / Apple Silicon) ==="
pyimgtag run \
  --input-dir "$INPUT_DIR" \
  --extensions heic \
  --verbose

echo ""
echo "Tip: use --write-exif to embed AI tags back into the EXIF metadata."
