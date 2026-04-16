#!/usr/bin/env bash
# Wiki: https://github.com/kurok/pyimgtag/wiki/Managing-Your-Library
# Manage tags: list, rename, delete, merge across the image database.
set -euo pipefail

echo "=== all tags with image counts ==="
pyimgtag tags list

echo ""
echo "=== rename 'golden-hour' → 'golden_hour' ==="
pyimgtag tags rename golden-hour golden_hour

echo ""
echo "=== merge 'city' and 'street' into 'urban' ==="
pyimgtag tags merge city street --into urban

echo ""
echo "=== delete 'screenshot' tag ==="
pyimgtag tags delete screenshot

echo ""
echo "=== updated tag list ==="
pyimgtag tags list
