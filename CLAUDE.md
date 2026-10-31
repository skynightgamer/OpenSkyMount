# CLAUDE.md — OpenSkyMount

Local-first alt-az mount control software + simulator. No physical hardware exists yet; never claim hardware
behaviour is verified.

## Start of every session
1. Read `docs/PROGRESS.md`, `docs/NEXT_TASKS.md` (pick the top unfinished item), skim `docs/DECISIONS.md`.
2. Environment: `backend/.venv` (create with `scripts/setup.sh` or `uv venv backend/.venv && uv pip install -p backend/.venv/bin/python -e "backend[dev]"`).
   If `skyfield-data` download hangs with uv, download the wheel with curl and install the file.
3. Run `scripts/test.sh` before and after changes. Keep it green.

## Commands
- Backend tests: `cd backend && .venv/bin/python -m pytest -q` (~45 s). Lint: `.venv/bin/ruff check openskymount tests`. Types: `.venv/bin/mypy openskymount`.
- Frontend: `cd frontend && npx tsc -p tsconfig.json && npx vitest run && npm run build` (build writes to `backend/openskymount/static/`, git-ignored).
- Browser E2E: start server, then `node frontend/e2e/smoke.cjs http://127.0.0.1:8000 <shot-dir>`.
- Run: `backend/.venv/bin/python -m openskymount.server [--host 0.0.0.0] [--driver tcp://host:port]`.

## Architecture (see docs/architecture.md)
- `astro/engine.py` — all astronomy via Skyfield/DE421 (offline). Azimuth from North through East. Unix time in, `engine.time()` converts.
- `mount/driver.py` — `MountDriver` ABC in *mount (axis) coordinates*; multi-turn azimuth.
- `mount/controller.py` — deterministic `update(sim_time, wall_time, epoch)`; owns GoTo/tracking/safety. Setpoints refer to the start of each control interval.
- `mount/sim_driver.py` + `mount/axis.py` — physics. `mount/protocol*.py`, `fake_firmware.py` — OSM/1 hardware path.
- `server/app.py` — `Observatory` (shared state, 20 Hz loop, 10 Hz broadcast), REST + `/ws`. Control authority in `server/sessions.py`.
- `solve/` — optional, experimental plate solving (cedar-solve).
- Frontend: `src/store.ts` (WS client/state), `src/sky/` (canvas sky map, projection math with unit tests), `src/mount/MountView.tsx` (three.js), `src/components/Panels.tsx`.

## Rules
- Safety: Sun GoTo blocked unless `allow_solar_targets` AND simulated driver. Never relax this on the hardware path.
  E-stop must stay accepted from every client. Manual slews need the dead-man refresh.
- Tests use deterministic `tests/conftest.py::Rig` (explicit sim time). Astronomy tests must use independent references.
- Keep UI honest: features must work or be labelled experimental/simulated.
- Update `docs/PROGRESS.md`, `docs/NEXT_TASKS.md`, `docs/TEST_RESULTS.md` when finishing work. Commit to the assigned branch; never merge to main.
