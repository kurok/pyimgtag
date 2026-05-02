#!/usr/bin/env bash
# macOS only — requires Apple Photos and photoscript
set -euo pipefail

echo "=== pyimgtag faces import-photos smoke test ==="

if ! python3 -c "import photoscript" 2>/dev/null; then
    echo "SKIP: photoscript not installed, install with: pip install photoscript"
    exit 0
fi

DB=$(mktemp -t test_faces_smoke.XXXXXX.db)
trap 'rm -f "$DB"' EXIT

# mktemp creates an empty file; remove so sqlite can initialise cleanly.
rm -f "$DB"

echo "Running: pyimgtag faces import-photos --db $DB"
if ! pyimgtag faces import-photos --db "$DB"; then
    echo "FAIL: command exited with $?"
    exit 1
fi

DB_PATH="$DB" python3 -c "
import os, sqlite3
c = sqlite3.connect(os.environ['DB_PATH'])
count = c.execute(\"SELECT COUNT(*) FROM persons WHERE source='photos'\").fetchone()[0]
print('Persons imported:', count)
"

echo "PASS: import-photos completed"
exit 0
