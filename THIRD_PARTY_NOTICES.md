# Third-Party Notices

OpenCompanion source code is licensed under the MIT License. Runtime dependencies, model assets, and bundled media keep their own upstream licenses.

This file is a working public-release inventory, not a substitute for the full license files shipped by package managers.

## Bundled Assets

- `app/frontend/assets/talkinghead/avatar.glb` comes from the TalkingHead example avatar `mpfb.glb`. The upstream notice says the example avatar is CC0. See `app/frontend/assets/talkinghead/NOTICE.md`.
- `companion/assets/avatar/default.glb` is the same CC0 TalkingHead/MPFB example avatar.
- `companion/assets/voices/*.wav` are generated local voice preview clips from the public Kokoro voice assets. Regenerate with `npm run generate-voices:force` after changing the voice list or Kokoro assets.

## Runtime Models Downloaded By The App

- Kokoro TTS model weights are expected to come from Kokoro/Kokoro-82M compatible releases. The upstream Hugging Face model card currently marks `hexgrad/Kokoro-82M` as Apache-2.0.
- `kokoro-onnx` Python package code is MIT licensed upstream, although the wheel metadata may not expose the license field.
- Whisper speech-to-text models used through `pywhispercpp`/`whisper.cpp` derive from OpenAI Whisper model weights, which OpenAI documents as MIT licensed.
- Ollama models are user-selected or downloaded at runtime. Their licenses vary by model and are not covered by this repository license.

## JavaScript Dependencies

Primary runtime dependencies:

- `@met4citizen/talkinghead`: MIT
- `electron-updater`: MIT
- `keytar`: MIT
- `three`: MIT
- `ws`: MIT

Development and packaging dependencies are mostly MIT/ISC/BSD/Apache-2.0. The full resolved Node dependency inventory is generated from `package-lock.json` in `THIRD_PARTY_NODE_DEPENDENCIES.md` and included in packaged builds.

## Python Dependencies

Direct Python requirements currently report these licenses from package metadata:

| Package | License |
| --- | --- |
| `openai` | Apache-2.0 |
| `pydantic` | MIT |
| `numpy` | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| `kokoro-onnx` | MIT upstream; wheel metadata incomplete |
| `soundfile` | BSD-3-Clause |
| `pywhispercpp` | MIT |
| `keyring` | MIT |
| `typing_extensions` | PSF-2.0 |
| `Pillow` | MIT-CMU |
| `pywin32` | PSF |
| `psutil` | BSD-3-Clause |
| `nvidia-ml-py` | BSD |
| `croniter` | MIT |
| `tree-sitter` | MIT |
| `tree-sitter-javascript` | MIT |

Python transitive dependencies should be audited before the first public release artifact.
