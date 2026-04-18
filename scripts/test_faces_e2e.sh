#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== pyimgtag faces E2E test suite ==="
echo ""

PASS=0
SKIP=0
FAIL=0

run_test() {
    local name="$1"
    local script="$2"

    echo "--- $name ---"
    set +e
    bash "$script"
    local exit_code=$?
    set -e

    if [ "$exit_code" -eq 0 ]; then
        # Distinguish PASS vs SKIP by checking last output line
        # Both exit 0; scripts print PASS or SKIP as last meaningful line
        echo ""
        PASS=$((PASS + 1))
    elif [ "$exit_code" -eq 2 ]; then
        # Reserved for skip-via-exit-2 if needed in future
        echo ""
        SKIP=$((SKIP + 1))
    else
        echo ""
        FAIL=$((FAIL + 1))
    fi
}

# Capture output to detect SKIP vs PASS for exit-0 scripts
run_test_tracked() {
    local name="$1"
    local script="$2"

    echo "--- $name ---"
    set +e
    output=$(bash "$script" 2>&1)
    local exit_code=$?
    set -e
    echo "$output"
    echo ""

    if [ "$exit_code" -ne 0 ]; then
        FAIL=$((FAIL + 1))
    elif echo "$output" | grep -q "^SKIP:"; then
        SKIP=$((SKIP + 1))
    else
        PASS=$((PASS + 1))
    fi
}

run_test_tracked "faces import-photos" "$SCRIPT_DIR/test_faces_import_photos.sh"
run_test_tracked "faces ui"            "$SCRIPT_DIR/test_faces_ui.sh"

echo "==============================="
echo " Results"
echo "==============================="
printf "  PASS : %d\n" "$PASS"
printf "  SKIP : %d\n" "$SKIP"
printf "  FAIL : %d\n" "$FAIL"
echo "==============================="

if [ "$FAIL" -ne 0 ]; then
    echo "RESULT: FAILED ($FAIL failure(s))"
    exit 1
fi

echo "RESULT: OK"
exit 0
