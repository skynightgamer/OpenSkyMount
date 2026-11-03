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
  scripts/e2e.sh
fi
echo "All checks passed."
