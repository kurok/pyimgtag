#!/usr/bin/env bash
# Local pre-PR smoke runner.
#
# Workflow:
#   1. Make sure the project + Playwright + Chromium are installed.
#   2. Start the unified dashboard on $PORT (default 8000) in the
#      background, with a tmp progress DB so we never touch the user's
#      real ~/.cache/pyimgtag/progress.db.
#   3. Wait for /health to flip to 200.
#   4. Run the regular unit-test suite.
#   5. Run the Playwright Chromium smoke suite under tests/e2e/.
#   6. Stop the app cleanly on success, failure, or Ctrl-C.
#
# Failure artefacts (screenshot + Playwright trace + uvicorn log) live
# under tests/e2e/artifacts/ — the CI workflow uploads the same paths.
#
# Usage:
#   scripts/test-smoke-local.sh
#   PORT=8765 scripts/test-smoke-local.sh         # custom port
#   PYIMGTAG_DB=~/my.db scripts/test-smoke-local.sh   # walk a real DB
#   PYIMGTAG_E2E_HEADLESS=0 scripts/test-smoke-local.sh   # see the browser

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
BASE_URL="${BASE_URL:-http://${HOST}:${PORT}}"
LOG_DIR="${LOG_DIR:-tests/e2e/artifacts}"
APP_LOG="$LOG_DIR/app.log"

mkdir -p "$LOG_DIR"

log()  { printf "\033[1;34m[smoke]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[smoke]\033[0m %s\n" "$*" >&2; }
fail() { printf "\033[1;31m[smoke]\033[0m %s\n" "$*" >&2; exit 1; }

APP_PID=""
cleanup() {
    if [[ -n "$APP_PID" ]] && kill -0 "$APP_PID" 2>/dev/null; then
        log "stopping dashboard (pid $APP_PID)"
        kill "$APP_PID" 2>/dev/null || true
        # Give it a beat to flush the access log, then SIGKILL if needed.
        for _ in $(seq 1 20); do
            kill -0 "$APP_PID" 2>/dev/null || break
            sleep 0.1
        done
        kill -9 "$APP_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

# 1. Install / update dependencies (skip if already on the right version).
if ! python3 -c "import pyimgtag" >/dev/null 2>&1; then
    log "installing pyimgtag editable + e2e extras"
    python3 -m pip install -e '.[review,e2e]' --quiet
fi

if ! python3 -c "import playwright.sync_api" >/dev/null 2>&1; then
    log "installing Playwright Python bindings"
    python3 -m pip install playwright --quiet
fi

if ! python3 -c "import requests" >/dev/null 2>&1; then
    log "installing requests"
    python3 -m pip install requests --quiet
fi

# Idempotent — Playwright skips work when Chromium already installed.
log "ensuring Chromium is installed"
python3 -m playwright install chromium --with-deps 2>&1 | tail -3 || true

# 2. Start the app in the background with a tmp DB.
TMP_DB="$(mktemp -t pyimgtag-smoke.XXXXXX.db)"
trap 'cleanup; rm -f "$TMP_DB"' EXIT INT TERM

log "starting dashboard on $BASE_URL (db=$TMP_DB)"
HOST="$HOST" PORT="$PORT" PYIMGTAG_DB="$TMP_DB" \
    python3 -m pyimgtag.webapp >"$APP_LOG" 2>&1 &
APP_PID=$!
log "  → pid $APP_PID, log $APP_LOG"

# 3. Wait for health.
log "waiting for /health"
DEADLINE=$(( $(date +%s) + 45 ))
HEALTHY=""
while [[ $(date +%s) -lt $DEADLINE ]]; do
    if curl -fsS --max-time 1 "$BASE_URL/health" >/dev/null 2>&1; then
        HEALTHY=1
        break
    fi
    if ! kill -0 "$APP_PID" 2>/dev/null; then
        warn "uvicorn exited before /health came up"
        cat "$APP_LOG" || true
        fail "dashboard failed to start"
    fi
    sleep 0.25
done
[[ -n "$HEALTHY" ]] || { cat "$APP_LOG" || true; fail "/health timeout"; }
log "  → healthy"

# 4. Unit tests. The default addopts in pyproject.toml ignores
# tests/e2e/ so this only runs the in-process suite; the e2e step
# below covers the live-dashboard smoke explicitly.
log "running unit tests"
python3 -m pytest tests/ -q

# 5. Playwright smoke.
log "running Playwright smoke (BASE_URL=$BASE_URL)"
BASE_URL="$BASE_URL" python3 -m pytest tests/e2e/ \
    --override-ini='addopts=' -v

log "all green. dashboard log: $APP_LOG"
