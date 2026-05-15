# Agent Guide

OpenCompanion is a local-first Electron desktop app with a Python backend. Keep public contributions focused, small, and easy to review.

## Required Reading Before Work

Before changing code, read these files in this order:

1. `AGENTS.md` or `CLAUDE.md` — the agent entry rules.
2. `docs/HANDOFF.md` — current state, verified commands, and next steps.
3. `docs/ARCHITECTURE.md` — runtime shape, configuration boundaries, and safety model.
4. `README.md` — the public project contract shown to users and reviewers.

Use `docs/HANDOFF.md` to decide what is currently being cleaned up or verified. Use `docs/ARCHITECTURE.md` before changing runtime behavior, config loading, model/provider flow, tool routing, safety boundaries, or profile/runtime state.

After meaningful changes:

- Update `docs/HANDOFF.md` when current state, next steps, or verification status changes.
- Update `docs/ARCHITECTURE.md` when runtime shape, configuration, provider behavior, tool safety, or data boundaries change.
- Update `README.md` only for user-facing setup, status, support, safety, or feature changes.
- Keep `AGENTS.md` and `CLAUDE.md` aligned when agent instructions change.

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

Use narrower tests when possible, then document any broader verification in `docs/HANDOFF.md`.

## Code Style

- Backend: Python.
- Frontend/runtime scripts: JavaScript and Electron.
- No TypeScript.
- Prefer existing modules and patterns over adding parallel abstractions.
- Keep files small, single-purpose, and boring in the good way.
- Put configuration in existing config paths rather than ad hoc files.
- Do not invent a new architecture layer unless `docs/ARCHITECTURE.md` is updated and the change is clearly justified.

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
- Update relevant docs when runtime behavior, setup, tests, or public release packaging changes.
- Keep public docs concise, current, and free of private session notes.
