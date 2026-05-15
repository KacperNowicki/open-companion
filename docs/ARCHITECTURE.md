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

The overlay companion scene owns its own readiness UI: the renderer shows an in-scene loading layer until the TalkingHead avatar readiness promise resolves and the browser has had a paint tick. If the avatar fails to load, the scene keeps a compact unavailable state visible instead of revealing a blank canvas.

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

## Dependency Supply Chain

Dependency installs should be lockfile-based. Use `npm ci` for Node dependencies and `python -m pip install --require-hashes -r requirements.lock` for Python dependencies. The helper command `npm run install:locked` runs both.

The project-level `.npmrc` is intentional supply-chain policy. It keeps npm audit enabled at moderate severity, requires `package-lock.json`, disables funding noise, and saves future npm dependencies as exact versions. Top-level npm dependencies in `package.json` should also stay exact-pinned to the vetted lockfile versions. Do not use `npm audit fix` blindly; it may pull newer dependency versions that are still inside quarantine or change transitive resolution in ways the age checker should review first.

Locked npm and PyPI package versions must be at least 28 days old before they are accepted. `app/scripts/check-dependency-age.js` reads `package-lock.json` and `requirements.lock`, checks npm registry and PyPI release metadata, and fails `npm test` when a locked version is still inside that quarantine window. Temporary exceptions, if ever needed for a security emergency, belong in `config/dependency-age-exceptions.json` with an expiry and reason.

## Test Mode

Electron and backend tests run with `OPEN_COMPANION_TEST_MODE=1`. In that mode, the reminder scheduler must stay disabled through both startup and config reloads unless a test explicitly opts in with `OPEN_COMPANION_TEST_ENABLE_SCHEDULER=1`. This keeps UI and capture profiles quiet and prevents stale local schedules from leaking into automated runs.

Test profile seeders should write an empty `companion/schedule.md`, empty `companion/memory/schedule_state.json`, and empty `companion/vault/todo.md` unless a test is explicitly covering reminders. The scheduler parser also ignores lines inside HTML comments, so starter markdown examples cannot become real reminders.

Public integration fixtures should not require a private root `config.json`. When a suite needs a profile config, seed a minimal test config unless the ignored local file exists.

## Runtime Caches And Logging

Runtime caches should be local, bounded, and easy to invalidate by normal file or process changes.

- Conversation history trimming counts each candidate message once and then drops old messages with a running total.
- Memory retrieval keeps an in-process embedding cache and persists it to `companion/memory/embedding_cache.json`; entries are keyed by model plus text hash and capped to avoid unbounded growth.
- Scheduler parsing caches `todo.md` and `schedule.md` reminders by resolved path, mtime, and size, then applies live scheduler state such as snoozes and fired markers each tick.
- Vault `list_files` and `search_files` stream visible paths and stop at their result limit instead of materializing the full tree.
- Electron Ollama metadata reads use `app/frontend/main/ollama-cache.js`, a short-lived main-process cache for `/api/tags`, `/api/ps`, and `/api/show` responses.
- Electron Python subprocess setup caches the `python -m site --user-site` probe per Python command.
- After the overlay is ready, Electron can prewarm Settings in a hidden reusable window so config/model/voice hydration is done before the user opens it. Disable with `OPEN_COMPANION_PREWARM_SETTINGS=0` or tune the delay with `OPEN_COMPANION_SETTINGS_PREWARM_DELAY_MS`.
- Companion startup warmup is speculative background work and must not block user turns. The first real user turn sets a warmup cancellation event; memory retrieval preload checks that event before expensive work and between entry embeddings, and the companion model warmup skips its tiny model request once user activity has begun.

Verbose per-turn backend request/result logging is disabled by default. Set `OPEN_COMPANION_VERBOSE_RUNTIME_LOGS=1` when debugging prompt, message, or Ollama payload flow. Exceptional warnings and keepalive/runtime status logs may still be emitted without that flag.

## ChatGPT OAuth

ChatGPT OAuth is opt-in and stores tokens in the OS keychain, never in config or the repository. The base keychain service defaults to `open-companion` in both Electron and direct Python runs. The OAuth keychain service is profile-scoped as `OPEN_COMPANION_CHATGPT_OAUTH_KEYCHAIN_SERVICE`, with a default derived from `OPEN_COMPANION_KEYCHAIN_SERVICE`, the literal `chatgpt-oauth`, and a hash of the resolved profile root. This prevents separate dev/public worktrees from silently sharing the same ChatGPT tokens.

Electron writes the resolved OAuth keychain service into the backend environment before launching Python. The Python provider reads only that service for OAuth tokens; it does not silently consume legacy global `OpenCompanion` OAuth credentials. Disconnect/logout clears the current profile-scoped service and legacy OAuth slots so stale credentials can be removed from Settings.

The OAuth token exchange and refresh grants use `application/x-www-form-urlencoded` PKCE requests and the ChatGPT Codex-compatible scope set `openid profile email offline_access`. The token store keeps only the access token, refresh token, expiry, and ChatGPT account id. Access-token JWT expiry is checked before refresh, and refresh is attempted only when the token is missing or near expiry. Account id is extracted from the access token claim at `https://api.openai.com/auth.chatgpt_account_id`, with `sub` as a fallback.

ChatGPT OAuth model calls follow the OpenClaw/pi-ai Codex route: the app uses the ChatGPT OAuth access token directly against `https://chatgpt.com/backend-api/codex/responses` and `wss://chatgpt.com/backend-api/codex/responses`. It does not exchange an ID token for an OpenAI API key. Python keeps a single Node bridge process alive per provider session; the bridge keeps one WebSocket open for sequential `response.create` turns, sends `chatgpt-account-id` when the account id is available, sends `originator: pi`, a `pi (...)` user agent, and `OpenAI-Beta: responses_websockets=2026-02-06`, then emits SSE-compatible events plus a per-turn `[DONE]` sentinel back to Python. The Codex transport rejects `max_output_tokens`, so OpenCompanion omits that parameter for ChatGPT OAuth requests. If the WebSocket handshake or turn fails, the provider falls back to the ChatGPT Codex SSE endpoint with `OpenAI-Beta: responses=experimental` and resets stale WebSocket continuation state when the server reports a missing previous response, missing OAuth scope, or connection limit.

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
