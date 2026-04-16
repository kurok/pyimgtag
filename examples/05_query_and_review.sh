#!/usr/bin/env bash
# Wiki: https://github.com/kurok/pyimgtag/wiki/Reviewing-Results
# Query the results DB with filters, then launch the web review UI.
set -euo pipefail

echo "=== query: outdoor high-significance photos ==="
pyimgtag query \
  --scene outdoor_leisure outdoor_travel \
  --significance high \
  --format table

echo ""
echo "=== query: images containing text ==="
pyimgtag query --has-text --format table

echo ""
echo "=== launching web review UI ==="
echo "Open http://127.0.0.1:8765 in your browser, then Ctrl-C to stop."
pyimgtag review
