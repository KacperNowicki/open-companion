const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");
const { pathToFileURL } = require("url");

const VOICE_NAMES = [
  "af_alloy",
  "af_aoede",
  "af_bella",
  "af_heart",
  "af_jessica",
  "af_kore",
  "af_nicole",
  "af_nova",
  "af_river",
  "af_sarah",
  "af_sky",
  "am_adam",
  "am_echo",
  "am_eric",
  "am_fenrir",
  "am_liam",
  "am_michael",
  "am_onyx",
  "am_puck",
  "am_santa",
  "bf_alice",
  "bf_emma",
  "bf_isabella",
  "bf_lily",
  "bm_daniel",
  "bm_fable",
  "bm_george",
  "bm_lewis",
];

function createVoicePreviewService({ projectRoot, runtimePaths, buildPythonSubprocessEnv }) {
  const previewsDir = runtimePaths.VOICE_PREVIEWS_DIR;

  function getPreviewPath(voice) {
    return path.join(previewsDir, `${voice}.wav`);
  }

  function isKnownVoice(voice) {
    return VOICE_NAMES.includes(voice);
  }

  async function checkMissingPreviews() {
    const missing = [];
    for (const voice of VOICE_NAMES) {
      if (!fs.existsSync(getPreviewPath(voice))) {
        missing.push(voice);
      }
    }
    return missing;
  }

  function getPreviewUrl(voice) {
    if (!isKnownVoice(voice)) {
      return null;
    }
    const previewPath = getPreviewPath(voice);
    if (!fs.existsSync(previewPath)) {
      return null;
    }
    return pathToFileURL(previewPath).href;
  }

  async function generatePreview(voice) {
    if (!isKnownVoice(voice)) {
      return { ok: false, error: "Unknown voice" };
    }
    const outPath = getPreviewPath(voice);
    if (fs.existsSync(outPath)) {
      return { ok: true };
    }
    if (runtimePaths.IS_PACKAGED) {
      return { ok: false, error: "Voice previews must be bundled with packaged builds." };
    }

    try {
      fs.mkdirSync(previewsDir, { recursive: true });
      const { pythonCommand, env } = buildPythonSubprocessEnv();
      await new Promise((resolve, reject) => {
        const proc = spawn(
          pythonCommand,
          [runtimePaths.BACKEND_GENERATE_PREVIEW_ENTRY, "--voice", voice, "--out", outPath],
          {
            cwd: projectRoot,
            stdio: ["ignore", "pipe", "pipe"],
            windowsHide: true,
            env,
          }
        );
        let stderr = "";
        proc.stderr.on("data", (chunk) => {
          stderr += String(chunk || "");
        });
        proc.on("exit", (code) => {
          if (code === 0) resolve();
          else reject(new Error(stderr.trim() || `Preview generator exited with code ${code}`));
        });
      });
      return { ok: true };
    } catch (err) {
      return { ok: false, error: String(err.message) };
    }
  }

  return {
    checkMissingPreviews,
    generatePreview,
    getPreviewUrl,
  };
}

module.exports = {
  VOICE_NAMES,
  createVoicePreviewService,
};
