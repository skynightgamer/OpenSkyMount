#!/usr/bin/env bash
# Start OpenSkyMount (serves the built UI). --lan binds to all interfaces for other devices.
set -euo pipefail
cd "$(dirname "$0")/.."
HOST=127.0.0.1
if [[ "${1:-}" == "--lan" ]]; then HOST=0.0.0.0; shift; fi
echo "Open http://localhost:8000  (LAN: http://<this-computer-ip>:8000 when started with --lan)"
exec backend/.venv/bin/python -m openskymount.server --host "$HOST" --port "${PORT:-8000}" --state-dir state "$@"
