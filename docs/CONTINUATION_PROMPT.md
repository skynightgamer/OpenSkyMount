# Continuation prompt for the next Claude Code session

Paste this into a new Claude Code session on the repository (branch `claude/gallant-turing-py2w4q`):

---
You are continuing development of **OpenSkyMount**, a local-first alt-az mount control app with a simulator
(FastAPI + Skyfield backend, React/TypeScript/three.js frontend). The full original specification is summarised in
`README.md`, `docs/architecture.md` and `CLAUDE.md`.

1. Read `CLAUDE.md`, `docs/PROGRESS.md`, `docs/NEXT_TASKS.md`, `docs/DECISIONS.md`.
2. Set up: `scripts/setup.sh` (or the uv commands in CLAUDE.md). Run `scripts/test.sh` and confirm it passes
   (77 backend + 6 frontend tests).
3. If NEXT_TASKS item 0 (GitHub write access) is still open, try `git push -u origin claude/gallant-turing-py2w4q`
   first.
4. Take the top open item in `docs/NEXT_TASKS.md`. Implement it in a well-scoped way with tests. Keep all existing
   tests green. Run the browser E2E (`scripts/test.sh --e2e`) if you touched the UI.
5. Update `docs/PROGRESS.md`, `docs/NEXT_TASKS.md`, `docs/TEST_RESULTS.md` (and `docs/DECISIONS.md` for design
   choices). Commit with clear messages and push to the same branch. Never merge to the default branch.
6. Safety rules: keep solar GoTo blocked off-simulator, keep E-stop available to all clients, and never claim
   hardware behaviour is verified.
Budget: prefer finishing one verified item over starting several.
---
