#!/usr/bin/env bash
set -euo pipefail

echo "=== pyimgtag faces ui smoke test ==="

if ! python3 -c "import fastapi, uvicorn, httpx" 2>/dev/null; then
    echo "SKIP: missing dependencies. Install with: pip install 'pyimgtag[review]'"
    exit 0
fi

PORT=18766
DB=/tmp/test_faces_ui_smoke.db
SERVER_PID=""

trap 'kill "$SERVER_PID" 2>/dev/null; rm -f "$DB"' EXIT

rm -f "$DB"

python3 - <<'PYEOF'
import sqlite3, sys

conn = sqlite3.connect('/tmp/test_faces_ui_smoke.db')
conn.executescript("""
CREATE TABLE IF NOT EXISTS persons (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    label   TEXT    NOT NULL DEFAULT '',
    confirmed INTEGER NOT NULL DEFAULT 0,
    source  TEXT    NOT NULL DEFAULT 'auto',
    trusted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS faces (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    image_path  TEXT    NOT NULL,
    bbox_x      INTEGER NOT NULL DEFAULT 0,
    bbox_y      INTEGER NOT NULL DEFAULT 0,
    bbox_w      INTEGER NOT NULL DEFAULT 0,
    bbox_h      INTEGER NOT NULL DEFAULT 0,
    confidence  REAL    NOT NULL DEFAULT 0.0,
    embedding   BLOB,
    person_id   INTEGER REFERENCES persons(id)
);
INSERT INTO persons (label, source) VALUES ('Seed Person', 'auto');
INSERT INTO faces (image_path, bbox_x, bbox_y, bbox_w, bbox_h, confidence, person_id)
    VALUES ('/tmp/face1.jpg', 10, 10, 50, 50, 0.95, 1);
INSERT INTO faces (image_path, bbox_x, bbox_y, bbox_w, bbox_h, confidence, person_id)
    VALUES ('/tmp/face2.jpg', 20, 20, 60, 60, 0.88, 1);
""")
conn.commit()
conn.close()
print("DB seeded.")
PYEOF

pyimgtag faces ui --db "$DB" --port "$PORT" &
SERVER_PID=$!

# Wait up to 5 seconds for the server to be ready
READY=0
for i in $(seq 1 10); do
    if curl -sf "http://127.0.0.1:${PORT}/api/persons" -o /dev/null 2>/dev/null; then
        READY=1
        break
    fi
    sleep 0.5
done

if [ "$READY" -eq 0 ]; then
    echo "FAIL: server did not become ready within 5 seconds"
    exit 1
fi

FAILURES=0

# GET /api/persons
CODE=$(curl -s -o /tmp/resp.json -w "%{http_code}" "http://127.0.0.1:${PORT}/api/persons")
if [ "$CODE" -ne 200 ]; then
    echo "FAIL: GET /api/persons returned $CODE"
    FAILURES=$((FAILURES + 1))
else
    COUNT=$(python3 -c "import json; d=json.load(open('/tmp/resp.json')); print(len(d) if isinstance(d, list) else d.get('total', '?'))" 2>/dev/null || echo "?")
    echo "OK: GET /api/persons -> 200 (count: $COUNT)"
fi

# GET /
CODE=$(curl -s -o /tmp/resp.json -w "%{http_code}" "http://127.0.0.1:${PORT}/")
if [ "$CODE" -ne 200 ]; then
    echo "FAIL: GET / returned $CODE"
    FAILURES=$((FAILURES + 1))
else
    echo "OK: GET / -> 200"
fi

# POST /api/persons/1/label
CODE=$(curl -s -o /tmp/resp.json -w "%{http_code}" \
    -X POST \
    -H "Content-Type: application/json" \
    -d '{"label":"Test Person"}' \
    "http://127.0.0.1:${PORT}/api/persons/1/label")
if [ "$CODE" -ne 200 ]; then
    echo "FAIL: POST /api/persons/1/label returned $CODE"
    FAILURES=$((FAILURES + 1))
else
    echo "OK: POST /api/persons/1/label -> 200"
fi

# GET /api/persons/1/faces
CODE=$(curl -s -o /tmp/resp.json -w "%{http_code}" "http://127.0.0.1:${PORT}/api/persons/1/faces")
if [ "$CODE" -ne 200 ]; then
    echo "FAIL: GET /api/persons/1/faces returned $CODE"
    FAILURES=$((FAILURES + 1))
else
    echo "OK: GET /api/persons/1/faces -> 200"
fi

# POST /api/faces/1/unassign
CODE=$(curl -s -o /tmp/resp.json -w "%{http_code}" \
    -X POST \
    "http://127.0.0.1:${PORT}/api/faces/1/unassign")
if [ "$CODE" -ne 200 ]; then
    echo "FAIL: POST /api/faces/1/unassign returned $CODE"
    FAILURES=$((FAILURES + 1))
else
    echo "OK: POST /api/faces/1/unassign -> 200"
fi

# DELETE /api/persons/1
CODE=$(curl -s -o /tmp/resp.json -w "%{http_code}" \
    -X DELETE \
    "http://127.0.0.1:${PORT}/api/persons/1")
if [ "$CODE" -ne 200 ]; then
    echo "FAIL: DELETE /api/persons/1 returned $CODE"
    FAILURES=$((FAILURES + 1))
else
    echo "OK: DELETE /api/persons/1 -> 200"
fi

kill "$SERVER_PID" 2>/dev/null
SERVER_PID=""

if [ "$FAILURES" -ne 0 ]; then
    exit 1
fi

echo "PASS: all API endpoints responded correctly"
exit 0
