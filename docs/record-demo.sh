#!/usr/bin/env bash
# Re-record the README demo GIF (docs/assets/demo.gif).
#
# Requires:
#   pip install -e .                                  # puts `pyimgtag` on PATH
#   asciinema (https://docs.asciinema.org) + agg (https://github.com/asciinema/agg)
#
# The bundled mock Ollama (examples/mock_ollama.py) returns deterministic,
# per-image tags, so the demo is reproducible without a GPU or a real model.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
DEMO=/tmp/pyimgtag-demo

rm -rf "$DEMO" && mkdir -p "$DEMO/photos"
cp "$REPO"/examples/fixtures/*.jpg "$DEMO/photos/"

python "$REPO/examples/mock_ollama.py" 11435 >/tmp/pyimgtag-mock.log 2>&1 &
MOCK=$!
trap 'kill $MOCK 2>/dev/null || true' EXIT
sleep 1

cat > /tmp/pyimgtag-demo-run.sh <<'RUN'
export PYIMGTAG_NO_UPDATE_CHECK=1
cd /tmp/pyimgtag-demo && rm -f demo.db
P=$'\033[1;32m$\033[0m '
typ(){ printf '%s' "$P"; local s="$1" i; for ((i=0;i<${#s};i++)); do printf '%s' "${s:i:1}"; sleep 0.02; done; printf '\n'; }
demo(){ typ "$1"; eval "$1"; echo; sleep "${2:-2}"; }
clear; sleep 0.4
demo "pyimgtag run --input-dir photos --ollama-url http://127.0.0.1:11435 --db demo.db" 2.5
demo "pyimgtag query --format table --db demo.db" 2.5
demo "pyimgtag query --tag beach --db demo.db" 2
demo "pyimgtag cleanup --db demo.db" 2.5
RUN

asciinema rec --window-size 120x32 -c "bash /tmp/pyimgtag-demo-run.sh" --overwrite /tmp/pyimgtag.cast
agg --font-size 15 /tmp/pyimgtag.cast "$REPO/docs/assets/demo.gif"
echo "wrote $REPO/docs/assets/demo.gif"
