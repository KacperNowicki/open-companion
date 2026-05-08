/**
 * generate-voices.js
 *
 * npm run generate-voices
 *
 * Generates voice preview WAV files for all Kokoro voices into
 * companion/assets/voices/[voice_name].wav using the text:
 *   "Hello, I'm [voice_name]. Nice to meet you."
 *
 * Skips any file that already exists on disk unless --force is passed.
 */
const { spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");
const { VOICE_NAMES } = require("../frontend/main/voice-previews");

const ROOT = path.resolve(__dirname, "..", "..");
const PREVIEWS_DIR = path.join(ROOT, "companion", "assets", "voices");
const SCRIPT = path.join(ROOT, "app", "backend", "generate_voice_preview.py");
const PYTHON = process.env.OPEN_COMPANION_PYTHON || process.env.PYTHON || (process.platform === "win32" ? "python" : "python3");
const FORCE = process.argv.includes("--force");

fs.mkdirSync(PREVIEWS_DIR, { recursive: true });

let generated = 0;
let skipped = 0;
let failed = 0;

for (const voice of VOICE_NAMES) {
  const outPath = path.join(PREVIEWS_DIR, `${voice}.wav`);
  if (!FORCE && fs.existsSync(outPath)) {
    console.log(`[skip] ${voice}.wav already exists`);
    skipped++;
    continue;
  }

  console.log(`[gen] ${voice} ...`);
  const result = spawnSync(PYTHON, [SCRIPT, "--voice", voice, "--out", outPath], {
    cwd: ROOT,
    stdio: "inherit",
  });

  if (result.status === 0) {
    console.log(`[ok] ${voice}.wav`);
    generated++;
  } else {
    console.error(`[fail] ${voice} - exit code ${result.status}`);
    failed++;
  }
}

console.log(`\nDone: ${generated} generated, ${skipped} skipped, ${failed} failed.`);
if (failed > 0) process.exit(1);
