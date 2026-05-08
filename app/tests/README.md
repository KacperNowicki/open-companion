# OpenCompanion Tests

## Cleanup Test Artifacts

Use the cleanup script's inspection modes before removing generated output:

```bash
npm run clean:artifacts:list
npm run clean:artifacts:dry-run
npm run clean:artifacts
```

The default cleanup groups cover build output, installer cache, logs, legacy root `memory/`, and test output directories including `test-results/`, `app/tests/test-results/`, and `app/tests/test-results-playwright/`. The script validates targets stay inside the repo and skips non-directories.

`npm run clean:runtime-assets` is available for downloaded runtime assets such as Kokoro files, but it is not part of the default cleanup path.

## Pick the Right Suite

```bash
# Fast config regression only (JS + Python, no Ollama)
npm test

# Backend / bridge smoke suite against the real runtime
npm run test:e2e
python app/tests/test_suite.py

# Updater controller smoke test
npm run test:updater

# Integration suite: code-level backend, provider, layer, memory, and heartbeat coverage
npm run test:integration

# Live companion behavior suite against Ollama
npm run test:companion

# Real Electron Settings-window suite (display required)
npm run test:settings-ui

# Config + bridge suites
npm run test:all

# Full matrix
npm run test:full
```

`npm run test:all` does not include `npm run test:settings-ui`, `npm run test:integration`, or `npm run test:companion`.

## What Each Suite Covers

- `npm test`: config loading, deep-merge behavior, profile migration coverage, and migration defaults in both Node and Python.
- `npm run test:e2e`: alias for `python app/tests/test_suite.py`.
- `python app/tests/test_suite.py`: backend imports, registry loading, bridge startup, companion conversation, tool execution, heartbeat behavior, and vault/config smoke checks.
- `npm run test:updater`: updater controller state-machine checks without Electron packaging or GitHub releases.
- `npm run test:integration`: standalone integration coverage for tools, layer policy, providers, memory, heartbeat, and skills. Network/provider behavior is mocked here.
- `npm run test:companion`: live end-to-end LLM behavior against real Ollama models using isolated temp profiles.
- `npm run test:settings-ui`: launches the real Electron app, opens the real Settings window, and verifies persistence plus live runtime effects.

## Preconditions

- `python app/tests/test_suite.py` can run without Ollama, but companion conversation, tool execution, and heartbeat sections are skipped when Ollama is offline.
- `npm run test:updater` does not require Ollama or a packaged build.
- `npm run test:integration` does not require Ollama. It uses mocked provider/network paths and temp directories.
- `npm run test:companion` requires Ollama plus an installed chat-capable model. If Ollama is unavailable, the runner skips gracefully and exits 0.
- `npm run test:settings-ui` requires a desktop session because it launches Electron.
- `python app/tests/test_suite.py` temporarily touches live runtime files and should not run in parallel with the app or with other suites.

## Recommended Workflow

- If you changed config loading, defaults, updater/profile migration behavior, or migration rules, run `npm test`.
- If you changed updater controller behavior or dev updater IPC wiring, run `npm run test:updater`.
- If you changed backend runtime behavior, bridge events, tools, memory, heartbeat, or wrapper logic, run `python app/tests/test_suite.py`.
- If you changed tool execution internals, provider behavior, heartbeat internals, memory internals, or skill loading, run `npm run test:integration`.
- If you changed real model behavior, skill prompts, live Ollama routing, or the bridge contract, run `npm run test:companion`.
- If you changed Settings UI, Electron IPC, overlay live-apply behavior, or persisted settings behavior, run `npm run test:settings-ui`.

## Windows / Playwright Note

Playwright artifacts default to `app/tests/test-results/`. If Windows locks a prior artifact folder, rerun with a separate artifact root:

```bash
npx playwright test --config app/tests/playwright.config.js app/tests/ui/settings-ui.spec.js --output app/tests/test-results-playwright
```

Current note: the Settings suite now reflects the post-TalkingHead settings surface and no longer expects the removed Avatar tab.
