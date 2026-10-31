#!/usr/bin/env bash
# One-time setup: Python backend venv + frontend build. Needs Python >= 3.11 and Node >= 18.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv backend/.venv
backend/.venv/bin/pip install --upgrade pip
backend/.venv/bin/pip install -e "backend[dev]"
# Optional extras (uncomment): independent astronomy cross-check, plate-solving PoC
# backend/.venv/bin/pip install astropy
# backend/.venv/bin/pip install --no-deps cedar-solve && backend/.venv/bin/pip install scipy pillow
(cd frontend && npm ci && npm run build)
echo "Setup complete. Start with: scripts/run.sh   (add --lan to allow phones/tablets on your network)"
