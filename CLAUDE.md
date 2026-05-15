# Claude Code Guide

OpenCompanion is a local-first Electron desktop app with a Python backend. Keep changes focused, small, and easy to review.

## Read First

Before editing code, read:

1. `CLAUDE.md` — this file.
2. `AGENTS.md` — shared rules for all coding agents.
3. `docs/HANDOFF.md` — current state, verified commands, and next steps.
4. `docs/ARCHITECTURE.md` — runtime shape, configuration boundaries, and safety model.
5. `README.md` — the public project description and user-facing setup notes.

Use `docs/HANDOFF.md` for the current cleanup state and what was last verified. Use `docs/ARCHITECTURE.md` before changing runtime behavior, config loading, provider/model behavior, tool safety, or profile/runtime state.

When you change behavior or cleanup state, update the matching doc in the same change:

- `docs/HANDOFF.md` for current state, verification, and next steps.
- `docs/ARCHITECTURE.md` for architecture, config, tool safety, provider, or runtime data changes.
- `README.md` for user-facing setup/status/safety/support changes.
- `AGENTS.md` and `CLAUDE.md` together if agent rules change.

## Start Here

- Install dependencies from lockfiles with `npm run install:locked`.
- If installing manually, use `npm ci` and `python -m pip install --require-hashes -r requirements.lock`.
- Run the desktop app with `npm start`.
- Run the backend directly with `python app/backend/wrapper.py` or `python app/backend/wrapper.py --json`.

## Common Tests

- `npm test`
- `npm run test:dependency-age`
- `npm run test:settings-ui`
- `npm run test:integration`
- `python app/tests/test_suite.py`

`python app/tests/test_suite.py` can take around 10 minutes and temporarily rewrites live runtime files. Do not run it beside the live app.

Prefer targeted verification first. Record important verification results in `docs/HANDOFF.md`.

## Code Style

- Backend: Python.
- Frontend/runtime scripts: JavaScript and Electron.
- No TypeScript.
- Prefer existing modules and patterns over adding parallel abstractions.
- Keep files small, single-purpose, and boring in the good way.
- Put configuration in existing config paths rather than ad hoc files.
- Avoid broad rewrites when a narrow cleanup is enough.

## Runtime Safety

- Do not add generic shell execution tools.
- Validate paths before filesystem operations.
- Reject path traversal before execution.
- Keep vault tools vault-relative.
- Keep `run_windows_terminal` approval-gated.
- Treat cloud APIs as opt-in only; local Ollama remains the default path.
- Do not silently expand tool permissions, host access, or runtime file access.

## Public Repo Hygiene

- Do not commit secrets, logs, local profile state, downloaded models, generated binaries, memory, vault contents, or active soul data.
- Do not accept dependency updates until every locked npm/PyPI version is at least 28 days old; `npm run test:dependency-age` enforces this quarantine.
- Keep the committed `.npmrc` in place. It enables npm audit, requires package-lock use, and saves future npm dependencies as exact versions.
- Do not run `npm audit fix` automatically; review the resulting lockfile and dependency age first.
- Keep third-party asset and dependency licenses reflected in `THIRD_PARTY_NOTICES.md`.
- Keep public docs concise, current, and free of private session notes.
