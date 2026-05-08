# Architecture

This document is the compact public architecture map for OpenCompanion. Keep it current when runtime behavior, configuration, tool safety, provider behavior, or runtime data boundaries change.

## Design Principles

- Local-first by default.
- Local Ollama is the default model path.
- Cloud providers are opt-in and require explicit user configuration.
- Runtime state belongs in local profile/runtime paths, not in the public source tree.
- Tool and host access should be explicit, scoped, and reviewable.
- Avoid parallel abstractions when existing modules can be extended cleanly.

## Runtime Shape

```text
Electron desktop shell
  -> preload/shared settings bridges
  -> renderer UI surfaces
  -> Node/Electron runtime services
  -> Python backend brain/runtime
  -> local models, configured cloud providers, tools, voice assets, profile state
```

The Electron app provides the desktop shell and UI. The Python backend handles the brain/runtime side and still supports direct backend execution for development and tests.

Brand imagery used by the app shell lives under `app/frontend/assets/brand/`. Public docs artwork can live under `docs/`, but runtime UI should reference frontend assets so packaged builds do not depend on documentation paths.

## Configuration

`config/defaults.js` is the canonical JavaScript default config. Runtime config is loaded from profile config files and merged with these defaults.

Settings option catalogs that need to be shared across UI surfaces live in:

```text
app/shared/settings-options.js
```

The defaults expose these catalogs at `settings_options` so packaged config state can override:

- onboarding model downloads
- curated Ollama discovery models
- cloud provider ordering and metadata
- model layer mapping
- heartbeat interval presets
- Kokoro voice metadata

Settings and onboarding preload scripts expose the same catalog for first paint. The renderers then apply the loaded config copy before populating controls, so a config override changes the UI without editing renderer code.

## Backend Defaults

`app/backend/brain.py` still has Python fallback defaults for direct backend execution. Those defaults should stay aligned with `config/defaults.js`, especially:

- layer names
- tool-call limits
- overlay defaults
- stale-config cleanup behavior
- local-first/cloud opt-in assumptions

When a default changes in one side, check the other side before committing.

## Test Mode

Electron and backend tests run with `OPEN_COMPANION_TEST_MODE=1`. In that mode, the reminder scheduler must stay disabled through both startup and config reloads unless a test explicitly opts in with `OPEN_COMPANION_TEST_ENABLE_SCHEDULER=1`. This keeps UI and capture profiles quiet and prevents stale local schedules from leaking into automated runs.

## Tool And Host Safety

OpenCompanion should not grow unrestricted host control by accident.

Current safety expectations:

- No generic shell execution tools.
- Filesystem operations must validate paths.
- Path traversal must be rejected before execution.
- Vault tools stay vault-relative.
- Windows terminal actions stay approval-gated.
- Cloud APIs stay opt-in.
- Tool permissions should not expand silently.

If this safety model changes, update this file and the agent guides in the same change.

## Runtime Data Boundaries

Do not commit runtime/private data:

- API keys
- OAuth secrets
- `.env` files with real values
- local profile state
- memory
- vault contents
- logs
- downloaded models
- generated runtime assets
- generated binaries
- active soul data

The public repository should contain source, docs, examples, safe defaults, and license/notice files, not private runtime state.

## Agent Maintenance Rules

Agents should read this file before changing runtime shape, configuration, provider behavior, tool routing, filesystem access, safety boundaries, or ignored runtime state.

Update `docs/HANDOFF.md` for current state and verification. Update this file for durable architecture decisions.
