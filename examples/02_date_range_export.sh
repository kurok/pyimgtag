#!/usr/bin/env bash
# Wiki: https://github.com/kurok/pyimgtag/wiki/Tagging-Your-Photos
# Tag a date range and export results to JSON and CSV.
set -euo pipefail

INPUT_DIR="${1:-~/Pictures/exported}"
DATE_FROM="${2:-2026-01-01}"
DATE_TO="${3:-2026-03-31}"
JSON_OUT="results_${DATE_FROM}_${DATE_TO}.json"
CSV_OUT="results_${DATE_FROM}_${DATE_TO}.csv"

echo "=== date-range tag + export ==="
echo "Date range: $DATE_FROM → $DATE_TO"
echo ""

pyimgtag run \
  --input-dir "$INPUT_DIR" \
  --date-from "$DATE_FROM" \
  --date-to "$DATE_TO" \
  --output-json "$JSON_OUT" \
  --output-csv "$CSV_OUT" \
  --verbose

echo ""
echo "Results written to $JSON_OUT and $CSV_OUT"
