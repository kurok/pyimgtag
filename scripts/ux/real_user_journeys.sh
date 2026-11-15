#!/usr/bin/env bash
# Real-user journeys over the generated corpus. Every command is run the way a
# user would type it; the transcript is the artefact we analyse.
set -u

LIB="${PYIMGTAG_UX_ROOT:-/lab/library}"
PHONE="$LIB/Phone Import 2024"
EXPORT="$LIB/Exported from chat"
OUT="${PYIMGTAG_UX_OUT:-/lab/out}"
mkdir -p "$OUT"

export OLLAMA_URL=http://127.0.0.1:11435
export PYIMGTAG_NO_WEB=1
export PYIMGTAG_NO_UPDATE_CHECK=1

run() {
    local title="$1"; shift
    {
        echo
        echo "################ $title"
        echo "\$ $*"
    } >> "$OUT/journeys.log"
    "$@" >> "$OUT/journeys.log" 2>&1
    echo "EXIT=$?" >> "$OUT/journeys.log"
}

# --------------------------------------------------------------------------
echo "=== JOURNEY 1: first import of a phone export (folder with a space)" >> "$OUT/journeys.log"
run "run over the phone import" pyimgtag run --input-dir "$PHONE"
run "status" pyimgtag status

echo "=== JOURNEY 2: 'only my July holiday'" >> "$OUT/journeys.log"
run "run --date-from/--date-to (dry-run)" pyimgtag run --input-dir "$PHONE" --date-from 2024-07-01 --date-to 2024-07-31 --dry-run
run "query --month 2024-07" pyimgtag query --month 2024-07
run "query --year 2023" pyimgtag query --year 2023

echo "=== JOURNEY 3: 'where was that photo?'" >> "$OUT/journeys.log"
run "query --tag beach" pyimgtag query --tag beach
run "query --city Paris" pyimgtag query --city Paris
run "query --country Switzerland" pyimgtag query --country Switzerland
run "query --bbox around Paris" pyimgtag query --bbox 48.80,2.20,48.92,2.50
run "query --bbox=-20,170,-10,-170 (negative first value)" pyimgtag query --bbox=-20,170,-10,-170
run "query --has-text" pyimgtag query --has-text
run "query --status ok --format json" pyimgtag query --status ok --format json

echo "=== JOURNEY 4: 'what should I throw away?'" >> "$OUT/journeys.log"
run "cleanup" pyimgtag cleanup
run "cleanup --include-review" pyimgtag cleanup --include-review
run "cleanup --format json" pyimgtag cleanup --format json

echo "=== JOURNEY 5: 'score them, keep the best'" >> "$OUT/journeys.log"
run "judge" pyimgtag judge --input-dir "$PHONE"
run "judge --min-score 8" pyimgtag judge --input-dir "$PHONE" --min-score 8
run "judge -v --limit 5" pyimgtag judge --input-dir "$PHONE" -v --limit 5
run "judge --output-json" pyimgtag judge --input-dir "$PHONE" --output-json "$OUT/ranking.json"

echo "=== JOURNEY 6: 'clean up the burst and the copies'" >> "$OUT/journeys.log"
run "dedup scan" pyimgtag dedup scan
run "dedup list" pyimgtag dedup list
run "dedup resolve (report only)" pyimgtag dedup resolve
run "dedup resolve --move-to" pyimgtag dedup resolve --move-to "$OUT/quarantine"
run "dedup undo" pyimgtag dedup undo

echo "=== JOURNEY 7: 'group my trip into albums'" >> "$OUT/journeys.log"
run "events detect" pyimgtag events detect
run "events list" pyimgtag events list
run "events apply --export-dir" pyimgtag events apply --export-dir "$OUT/albums"

echo "=== JOURNEY 8: 'what is in my library?'" >> "$OUT/journeys.log"
run "insights" pyimgtag insights
run "insights --output html" pyimgtag insights --output "$OUT/report.html"

echo "=== JOURNEY 9: 'give it to my photo manager'" >> "$OUT/journeys.log"
run "export csv" pyimgtag export --format csv --output "$OUT/library.csv"
run "export json" pyimgtag export --format json --output "$OUT/library.json"
run "export digikam" pyimgtag export --format digikam --output "$OUT/tags.xml"
run "cat the digikam tree" cat "$OUT/tags.xml"

echo "=== JOURNEY 10: 'write the tags into the files'" >> "$OUT/journeys.log"
rm -rf "$OUT/writeback" && cp -r "$PHONE" "$OUT/writeback"
run "run --write-exif" pyimgtag run --input-dir "$OUT/writeback" --write-exif
run "exiftool read-back" exiftool -s -G1 -Keywords -Description "$OUT/writeback/IMG_20240612_081533.jpg"

echo "=== JOURNEY 11: 'tag new photos as they arrive'" >> "$OUT/journeys.log"
run "watch --max-cycles 2" pyimgtag watch --input-dir "$OUT/writeback" --interval 5 --max-cycles 2 --no-web

echo "=== JOURNEY 12: 'let my assistant query it'" >> "$OUT/journeys.log"
run "mcp list_events + search_photos" bash -c 'printf "%s\n" \
"{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2024-11-05\",\"capabilities\":{},\"clientInfo\":{\"name\":\"t\",\"version\":\"1\"}}}" \
"{\"jsonrpc\":\"2.0\",\"method\":\"notifications/initialized\"}" \
"{\"jsonrpc\":\"2.0\",\"id\":3,\"method\":\"tools/call\",\"params\":{\"name\":\"list_events\",\"arguments\":{}}}" \
"{\"jsonrpc\":\"2.0\",\"id\":4,\"method\":\"tools/call\",\"params\":{\"name\":\"query_photos\",\"arguments\":{\"tag\":\"beach\",\"limit\":3}}}" \
| pyimgtag mcp'

echo "=== JOURNEY 13: the awkward files" >> "$OUT/journeys.log"
mkdir -p "$OUT/only-text" && echo hello > "$OUT/only-text/readme.txt"
run "run over a folder with no photos" pyimgtag run --input-dir "$OUT/only-text"
run "run over the broken .CR2" pyimgtag run --input-dir "$PHONE" --limit 100
run "status after the mixed run" pyimgtag status
run "reprocess --status error" pyimgtag reprocess --status error

echo "=== JOURNEY 14: the other library (no EXIF at all)" >> "$OUT/journeys.log"
run "run over the chat export" pyimgtag run --input-dir "$EXPORT"
run "query --tag from the chat export" pyimgtag query --tag beach --format paths
run "query --year on files without EXIF dates" pyimgtag query --year 2026

echo "=== JOURNEY 15: videos" >> "$OUT/journeys.log"
run "run --include-video over the clips" pyimgtag run --input-dir "$LIB/Clips" --include-video
run "query --type video" pyimgtag query --type video

echo "=== JOURNEY 16: tags" >> "$OUT/journeys.log"
run "tags list" pyimgtag tags list
run "tags rename" pyimgtag tags rename beach seaside
run "tags merge" pyimgtag tags merge seaside beach

echo
echo "=== DONE ===" >> "$OUT/journeys.log"
