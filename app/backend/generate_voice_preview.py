"""
generate_voice_preview.py

Generates a single voice preview WAV file using the Kokoro TTS backend.
Called by the npm generate-voices script.

Usage:
  python generate_voice_preview.py --voice af_nova --out path/to/out.wav
"""
import argparse
import base64
import os
import sys

# Ensure app/backend is on the path when called from project root
sys.path.insert(0, os.path.dirname(__file__))

from tts import KokoroTTS


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--voice", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)

    config = {
        "voice": {
            "kokoro_voice": args.voice,
            "tts_speed": 1.0,
        }
    }

    tts = KokoroTTS(config)

    if not tts.is_available():
        print(f"[preview] Kokoro not available (model files missing or kokoro-onnx not installed)", file=sys.stderr)
        sys.exit(1)

    text = f"Hello, I'm {args.voice}. Nice to meet you."
    result = tts.synthesize(text)

    if result is None:
        print(f"[preview] synthesis returned None for voice {args.voice}", file=sys.stderr)
        sys.exit(1)

    b64, _sample_rate = result
    wav_bytes = base64.b64decode(b64)

    with open(args.out, "wb") as f:
        f.write(wav_bytes)

    size = os.path.getsize(args.out)
    print(f"[preview] wrote {args.out} ({size} bytes)", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
