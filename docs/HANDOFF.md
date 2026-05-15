# Handoff

This file tracks the current public-repo cleanup state for maintainers and coding agents. Keep it concise, current, and free of private session notes.

## How To Use This File

Read this file before starting cleanup or release work. Use it to understand what was last verified and what should happen next.

After a meaningful change, update:

- `Current State` when repo status changes.
- `Verification` when commands are run or intentionally skipped.
- `Next Steps` when the remaining work changes.

Use `docs/ARCHITECTURE.md` for durable runtime/config/safety decisions. Use this file for the moving handoff between work sessions.

## Current State

- Public repo preparation is in progress.
- Runtime and settings defaults are consolidated through `config/defaults.js`.
- User-facing settings catalogs live in `app/shared/settings-options.js` and are exposed as `DEFAULT_CONFIG.settings_options`.
- Settings and onboarding preload scripts expose those catalogs to the renderer, and both renderers re-apply any loaded `settings_options` from config before hydrating UI state.
- Backend Python fallback defaults in `app/backend/brain.py` are aligned with the JS defaults for layer tool-call limits, overlay dimensions, and stale companion fields.
- The README includes `docs/assets/opencompanion-demo.gif`, generated from a real Electron test-mode capture with `npm run capture-readme-gif`.
- Test-mode backend launches do not start the reminder scheduler unless `OPEN_COMPANION_TEST_ENABLE_SCHEDULER=1` is set.
- README GIF capture and settings UI test profiles seed TTS/STT off to avoid audible reminder or speech leaks.
- Test-mode backend launches and later config reloads do not start the reminder scheduler unless `OPEN_COMPANION_TEST_ENABLE_SCHEDULER=1` is set.
- Test profile seeders write quiet reminder files, and scheduler markdown ignores HTML comments so starter examples cannot fire as reminders.
- README uses `docs/opencompanion-github.png`; the app shell uses `app/frontend/assets/brand/icon.png` rendered from the final `docs/opencompanion_logo.svg` for the main overlay, settings, onboarding, and Electron window icons.
- Windows packaging uses `app/frontend/assets/brand/icon.ico`, the runtime sets AppUserModelID `com.opencompanion.app`, and `app/scripts/electron-builder-after-pack.js` stamps the unpacked executable with `rcedit.exe` so taskbar/shortcut identity does not fall back to Electron.
- Keep `signAndEditExecutable: false` unless the Windows build environment can extract `winCodeSign`; the default electron-builder resource-edit path failed on this machine because the current user cannot create the symlinks in the `winCodeSign` package.
- Public support wording should use optional support/tip/supporter-token language, not donation/charity language.
- Runtime performance pass is in place: conversation trimming is linear, noisy per-turn stderr logging is opt-in via `OPEN_COMPANION_VERBOSE_RUNTIME_LOGS=1`, vault list/search stream through files, memory embeddings persist in a bounded sidecar cache, scheduler reminder parsing is mtime/size cached, Electron Ollama metadata calls share a short-lived cache, Python user-site probing is cached per Python command, and Settings can prewarm in a hidden reusable window after overlay startup.
- Companion startup warmup is non-blocking for real user turns. A user turn cancels the background warmup signal immediately, memory retrieval preload exits at cancellation checkpoints, and the companion model warmup skips its tiny model poke once user activity begins.
- Dependency installs now use lockfiles and a 28-day supply-chain quarantine. Use `npm run install:locked` for `npm ci` plus hashed Python installs from `requirements.lock`; `npm test` and `npm run test:all` run the dependency-age unit test and live npm/PyPI lockfile age check before the rest of the suite.
- The project-level `.npmrc` is intentional and should be committed: it enables npm audit at moderate severity, requires package-lock use, disables funding noise, and saves future npm dependency changes as exact versions. Top-level npm dependencies in `package.json` are exact-pinned to the currently vetted lockfile versions.
- `config/dependency-age-exceptions.json` currently contains a temporary security exception for `@xmldom/xmldom@0.9.10` expiring on 2026-05-17. The exception exists because older age-compliant releases are deprecated for critical issues; after expiry, the checker warns that the exception is stale and the locked version is old enough to pass without it.
- The overlay scene now shows an in-scene loading veil while the TalkingHead avatar initializes, then reveals the avatar after the readiness promise resolves and a paint tick completes. Load failures keep a compact unavailable state visible instead of leaving a blank scene.
- Public-clone integration fixtures no longer require an ignored root `config.json`; memory, file-tool, and context-lifecycle tests seed a minimal config when needed.
- ChatGPT OAuth credentials are now profile-scoped in the OS keychain through `OPEN_COMPANION_CHATGPT_OAUTH_KEYCHAIN_SERVICE`, and both Electron and direct Python default the base keychain service to `open-companion`. The app no longer treats legacy global `OpenCompanion` OAuth tokens as an active login for a different worktree, while disconnect still clears current and legacy OAuth slots. Backend refresh uses the same form-encoded token grant as login and stores only access/refresh/expires/account-id values.
- ChatGPT OAuth now matches the OpenClaw/pi-ai Codex OAuth shape: it requests `openid profile email offline_access`, keeps the `id_token_add_organizations`, `codex_cli_simplified_flow`, and originator authorize hints, and extracts `chatgpt_account_id` from the access token instead of requiring an ID token.
- ChatGPT OAuth model calls use the ChatGPT Codex responses endpoint directly with the stored OAuth access token: `https://chatgpt.com/backend-api/codex/responses` and `wss://chatgpt.com/backend-api/codex/responses`. There is no ID-token-to-OpenAI-API-key exchange. WebSocket calls use a persistent Node bridge process, send `chatgpt-account-id`, `originator: pi`, a `pi (...)` user agent, and `OpenAI-Beta: responses_websockets=2026-02-06`, omit `max_output_tokens`, and preserve `store=false` continuation for tool-result follow-ups. WebSocket failures still fall back to the ChatGPT Codex SSE endpoint with `OpenAI-Beta: responses=experimental`.
- A tracked/unignored credential scan found no actual ChatGPT OAuth tokens, JWTs, bearer tokens, or API keys in the public worktree. The previously visible OAuth login came from Windows Credential Manager under the old global `OpenCompanion` service, not from repository files.

## Verification

Latest focused verification on 2026-05-15:

```bash
python -m py_compile app/backend/wrapper.py app/backend/memory.py app/tests/integration/test_layers.py app/tests/integration/test_memory.py
python app/tests/integration/test_layers.py
python app/tests/integration/test_memory.py
node --check app/scripts/check-dependency-age.js
node --check app/tests/dependency-age.test.js
node app/tests/dependency-age.test.js
npm run test:dependency-age
npm run test:dependency-age -- --npm-only
npm run test:dependency-age -- --pypi-only
node app/scripts/check-dependency-age.js --npm-only --now 2026-05-18T00:00:00.000Z
npm config list --location=project
npm install --package-lock-only --ignore-scripts
node -e "<top-level npm package.json specs are exact and match package-lock>"
npm ci --ignore-scripts
node node_modules/electron/install.js
python -m pip install --dry-run --require-hashes -r requirements.lock
npm test
git diff --check
```

All commands passed. `npm ci --ignore-scripts` intentionally skipped Electron's postinstall, so `node node_modules/electron/install.js` was run before `npm test`. `git diff --check` emitted only line-ending normalization warnings. npm audit output currently reports 3 moderate vulnerabilities after the secure XML override; do not run `npm audit fix` automatically because it can pull dependency updates that are still inside the 28-day quarantine.

Previous broad verification on 2026-05-10:

```bash
python -m py_compile app/backend/memory.py app/backend/scheduler.py app/backend/tools/builtin/files.py app/backend/wrapper.py app/backend/wrapper_text.py app/backend/providers/gemma.py app/tests/integration/test_memory.py app/tests/integration/test_scheduler_realistic.py
node --check app/frontend/main.js
node --check app/frontend/main/ollama-cache.js
node --check app/tests/config.test.js
node app/tests/config.test.js
python app/tests/integration/test_memory.py
python app/tests/integration/test_file_tools.py
python app/tests/integration/test_scheduler_realistic.py
npm test
npm run test:integration
npm run test:settings-ui -- --output app/tests/test-results-playwright-settings-prewarm-rerun
node --check app/frontend/renderer.js
node app/tests/config.test.js
git diff --check
npm run test:settings-ui -- --output app/tests/test-results-playwright-scene-loading
node --check app/frontend/main.js
python -m py_compile app/backend/providers/chatgpt_oauth.py app/tests/integration/test_providers.py
python app/tests/integration/test_providers.py
npm test
git diff --check
npm run test:settings-ui -- --output app/tests/test-results-playwright-oauth-profile-keychain
node --check app/backend/chatgpt_oauth_ws_bridge.js
python -m py_compile app/backend/providers/chatgpt_oauth.py app/tests/integration/test_providers.py
node --check app/tests/config.test.js
node app/tests/config.test.js
python app/tests/integration/test_providers.py
node -e "<inline local ws-server smoke for app/backend/chatgpt_oauth_ws_bridge.js persistent two-turn reuse>"
npm test
npm run test:integration
git diff --check
node --check app/frontend/main.js
node --check app/frontend/settings-renderer.js
node --check app/backend/chatgpt_oauth_ws_bridge.js
python -m py_compile app/backend/runtime_paths.py app/backend/providers/chatgpt_oauth.py app/tests/integration/test_providers.py
node app/tests/config.test.js
python app/tests/integration/test_providers.py
python -c "<redacted ChatGPT OAuth keychain/scope probe>"
npm test
npm run test:integration
git diff --check
python -c "<redacted live ChatGPT OAuth WebSocket probe>"
```

All commands passed. `git diff --check` emitted only line-ending normalization warnings. A redacted repository scan also reported: `No credential-shaped values found in scanned repo files.` The latest live ChatGPT OAuth WebSocket probe used the stored profile-scoped OAuth token against `wss://chatgpt.com/backend-api/codex/responses` and returned `{"ok": true, "content": "websocket ok", "tool_calls": 0, "response_id_present": true}`.

Last known verification set:

```bash
npm test
npm run test:settings-ui -- --output app/tests/test-results-playwright-config-options
python -m py_compile app/backend/brain.py
npm run capture-readme-gif
npm run test:settings-ui -- --output app/tests/test-results-playwright-silent-test-profile
node --check app/frontend/main.js
git diff --check
python -m py_compile app/backend/wrapper.py
npm run test:settings-ui -- --output app/tests/test-results-playwright-brand-icon-rerun
npm run capture-readme-gif
npm run pack:win
node --check app/frontend/main.js
node --check app/scripts/electron-builder-after-pack.js
node -e "JSON.parse(require('fs').readFileSync('package.json','utf8')); console.log('package ok')"
git diff --check
python -m py_compile app/backend/scheduler.py app/backend/wrapper_scheduler.py app/tests/companion/conftest.py
python app/tests/integration/test_tools.py
node --check app/scripts/capture-readme-gif.js
npx playwright test app/tests/ui/settings-ui.spec.js --list
npm run test:settings-ui -- --output app/tests/test-results-playwright-reminder-clean
node --check app/frontend/settings-preload.js
node --check app/frontend/settings-renderer.js
node --check app/frontend/main/voice-previews.js
node app/tests/config.test.js
npm run test:settings-ui -- --output app/tests/test-results-playwright-audio-preview-fix3
```

The latest `npm run pack:win` produced `release/win-unpacked/OpenCompanion.exe`; its associated Windows icon was extracted to `build/opencompanion-exe-icon-final.png` and visually verified as the OpenCompanion logo.

The latest reminder cleanup removed stale local OpenCompanion temp profiles and ignored Playwright result folders. The follow-up audio-preview fix made Windows file preview URLs use `pathToFileURL`, simulates preview duration in Electron test mode when audio output is unavailable, and preserves expanded voice groups across the async preview refresh. `npm run test:settings-ui -- --output app/tests/test-results-playwright-audio-preview-fix3` passes 10/10.

When running new verification, record the exact commands and whether they passed.

## Next Steps

- Review remaining generated/runtime folders and make sure they are ignored.
- Keep `README.md` accurate for the public state of the app.
- Continue reviewing which remaining constants are presentation copy versus real runtime configuration.
- Add or trim public docs only when they help outside users or contributors.
- Add a short `CONTRIBUTING.md` later if outside contributions become realistic.
- Add a short `SECURITY.md` later if public vulnerability reporting or binary distribution becomes active.
- Keep `docs/ARCHITECTURE.md` aligned with config/runtime/tool safety changes.

## Do Not Commit

- Secrets
- API keys
- OAuth secrets
- logs
- local profile state
- downloaded models
- generated binaries
- memory
- vault contents
- active soul data
- private session notes
