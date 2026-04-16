#!/usr/bin/env bash
# Wiki: https://github.com/kurok/pyimgtag/wiki/Managing-Your-Library
# List photos the AI flagged for deletion or review.
set -euo pipefail

echo "=== photos flagged for deletion ==="
pyimgtag cleanup

echo ""
echo "=== also include 'review' candidates ==="
pyimgtag cleanup --include-review

echo ""
echo "Tip: these are read-only listings. Delete files manually after inspection."
