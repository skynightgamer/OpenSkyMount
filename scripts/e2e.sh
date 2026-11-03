#!/usr/bin/env bash
# Browser end-to-end tests: builds the UI, starts two servers (open and password mode) and runs the Playwright
# scripts against them. Needs backend/.venv, frontend/node_modules and Playwright with Chromium.
# Env: SHOTS (screenshot dir, default: a temp dir), LOG_DIR (server logs, default: a temp dir).
set -euo pipefail
cd "$(dirname "$0")/.."
SHOTS="${SHOTS:-$(mktemp -d)}"
LOG_DIR="${LOG_DIR:-$(mktemp -d)}"
mkdir -p "$SHOTS" "$LOG_DIR"
PY=backend/.venv/bin/python

(cd frontend && npm run build)
$PY -m openskymount.server --port 8765 --state-dir "$(mktemp -d)" --no-alpaca-discovery > "$LOG_DIR/osm-e2e.log" 2>&1 &
PID=$!
OPENSKYMOUNT_PASSWORD=e2e-secret-pw $PY -m openskymount.server --port 8767 --state-dir "$(mktemp -d)" \
  --no-alpaca-discovery > "$LOG_DIR/osm-e2e-auth.log" 2>&1 &
PID2=$!
$PY -m openskymount.server --port 8769 --state-dir "$(mktemp -d)" --no-alpaca-discovery > "$LOG_DIR/osm-e2e-features.log" 2>&1 &
PID3=$!
trap 'kill $PID $PID2 $PID3 2>/dev/null || true' EXIT

for port in 8765 8767 8769; do
  for _ in $(seq 1 100); do
    if curl -fsS "http://127.0.0.1:$port/api/health" > /dev/null 2>&1; then break; fi
    sleep 0.2
  done
  curl -fsS "http://127.0.0.1:$port/api/health" > /dev/null || { echo "server on port $port did not start"; cat "$LOG_DIR"/*.log; exit 1; }
done

(cd frontend && node e2e/smoke.cjs http://127.0.0.1:8765 "$SHOTS")
(cd frontend && node e2e/auth.cjs http://127.0.0.1:8767 e2e-secret-pw)
(cd frontend && node e2e/features.cjs http://127.0.0.1:8769 "$SHOTS")
echo "E2E passed (screenshots: $SHOTS, logs: $LOG_DIR)"
