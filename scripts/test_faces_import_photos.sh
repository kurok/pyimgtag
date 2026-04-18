#!/usr/bin/env bash
# macOS only — requires Apple Photos and photoscript
set -euo pipefail

echo "=== pyimgtag faces import-photos smoke test ==="

if ! python3 -c "import photoscript" 2>/dev/null; then
    echo "SKIP: photoscript not installed, install with: pip install photoscript"
    exit 0
fi

DB=/tmp/test_faces_smoke.db
rm -f "$DB"

echo "Running: pyimgtag faces import-photos --db $DB"
if ! pyimgtag faces import-photos --db "$DB"; then
    echo "FAIL: command exited with $?"
    exit 1
fi

python3 -c "
import sqlite3
c = sqlite3.connect('$DB')
count = c.execute(\"SELECT COUNT(*) FROM persons WHERE source='photos'\").fetchone()[0]
print('Persons imported:', count)
"

echo "PASS: import-photos completed"
exit 0
