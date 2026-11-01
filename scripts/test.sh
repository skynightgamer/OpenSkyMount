#!/usr/bin/env bash
# Full verification: lint, type check, backend tests, frontend type check + unit tests.
# Pass --e2e to also run the browser test (needs Playwright + Chromium and a built UI).
set -euo pipefail
cd "$(dirname "$0")/.."
backend/.venv/bin/ruff check backend/openskymount backend/tests
(cd backend && .venv/bin/mypy openskymount)
(cd backend && .venv/bin/python -m pytest -q)
(cd frontend && npx tsc -p tsconfig.json && npx vitest run)
if [[ "${1:-}" == "--e2e" ]]; then
  (cd frontend && npm run build)
  backend/.venv/bin/python -m openskymount.server --port 8765 --state-dir "$(mktemp -d)" > /tmp/osm-e2e.log 2>&1 &
  PID=$!
  OPENSKYMOUNT_PASSWORD=e2e-secret-pw backend/.venv/bin/python -m openskymount.server --port 8767 --state-dir "$(mktemp -d)" > /tmp/osm-e2e-auth.log 2>&1 &
  PID2=$!
  trap 'kill $PID $PID2' EXIT
  sleep 3
  (cd frontend && node e2e/smoke.cjs http://127.0.0.1:8765 "${SHOTS:-/tmp}")
  (cd frontend && node e2e/auth.cjs http://127.0.0.1:8767 e2e-secret-pw)
fi
echo "All checks passed."
