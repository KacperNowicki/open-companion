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

## Verification

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
