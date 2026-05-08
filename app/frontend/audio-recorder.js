/**
 * Audio Recorder — Mic capture to base64 WAV
 * ============================================
 * Captures microphone audio via MediaRecorder, then re-encodes the result as
 * 16-bit 16 kHz mono WAV and returns it as a base64 string suitable for
 * transport over the JSONL IPC bridge to the Python backend.
 *
 * Usage:
 *   const recorder = createAudioRecorder({
 *     onComplete: (base64Wav) => { ... },
 *     onError: (error) => { ... },
 *   });
 *   await recorder.startRecording();
 *   recorder.stopRecording(); // triggers onComplete asynchronously
 */

const TARGET_SAMPLE_RATE = 16000;

/**
 * Encode a Float32Array of mono PCM samples into a WAV file as a Uint8Array.
 * Outputs 16-bit PCM at the given sample rate with a standard RIFF header.
 */
function encodeWav(samples, sampleRate) {
  const numSamples = samples.length;
  const bytesPerSample = 2;
  const dataSize = numSamples * bytesPerSample;
  const buffer = new ArrayBuffer(44 + dataSize);
  const view = new DataView(buffer);

  function writeString(offset, str) {
    for (let i = 0; i < str.length; i++) {
      view.setUint8(offset + i, str.charCodeAt(i));
    }
  }

  // RIFF header
  writeString(0, "RIFF");
  view.setUint32(4, 36 + dataSize, true);
  writeString(8, "WAVE");

  // fmt chunk
  writeString(12, "fmt ");
  view.setUint32(16, 16, true);          // chunk size
  view.setUint16(20, 1, true);           // PCM format
  view.setUint16(22, 1, true);           // mono
  view.setUint32(24, sampleRate, true);  // sample rate
  view.setUint32(28, sampleRate * bytesPerSample, true); // byte rate
  view.setUint16(32, bytesPerSample, true); // block align
  view.setUint16(34, 16, true);          // bits per sample

  // data chunk
  writeString(36, "data");
  view.setUint32(40, dataSize, true);

  // Convert float samples to 16-bit PCM
  let offset = 44;
  for (let i = 0; i < numSamples; i++) {
    const clamped = Math.max(-1, Math.min(1, samples[i]));
    const int16 = clamped < 0 ? clamped * 0x8000 : clamped * 0x7FFF;
    view.setInt16(offset, int16, true);
    offset += 2;
  }

  return new Uint8Array(buffer);
}

/**
 * Downsample an AudioBuffer to mono at the target sample rate and return
 * a Float32Array of the resampled PCM data.
 */
async function resampleToMono(audioBuffer, targetRate) {
  const offlineCtx = new OfflineAudioContext(
    1,
    Math.ceil(audioBuffer.duration * targetRate),
    targetRate,
  );

  const source = offlineCtx.createBufferSource();
  source.buffer = audioBuffer;
  source.connect(offlineCtx.destination);
  source.start(0);

  const rendered = await offlineCtx.startRendering();
  return rendered.getChannelData(0);
}

/**
 * Convert a Uint8Array to a base64 string.
 */
function uint8ToBase64(bytes) {
  let binary = "";
  for (let i = 0; i < bytes.length; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary);
}

export function createAudioRecorder(callbacks = {}) {
  let mediaRecorder = null;
  let mediaStream = null;
  let chunks = [];
  let recording = false;

  async function startRecording() {
    if (recording) {
      return false;
    }

    try {
      mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (error) {
      throw new Error("Microphone access denied or unavailable.");
    }

    try {
      chunks = [];
      recording = true;
      mediaRecorder = new MediaRecorder(mediaStream);

      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          chunks.push(event.data);
        }
      };

      mediaRecorder.onstop = async () => {
        recording = false;

        // Release the mic stream immediately
        if (mediaStream) {
          for (const track of mediaStream.getTracks()) {
            track.stop();
          }
          mediaStream = null;
        }

        if (chunks.length === 0) {
          callbacks.onError?.(new Error("No audio was captured."));
          return;
        }

        try {
          const blob = new Blob(chunks, { type: mediaRecorder.mimeType });
          chunks = [];

          // Decode the recorded audio into an AudioBuffer
          const arrayBuffer = await blob.arrayBuffer();
          const audioCtx = new AudioContext();
          const audioBuffer = await audioCtx.decodeAudioData(arrayBuffer);
          await audioCtx.close();

          // Resample to 16 kHz mono for whisper.cpp
          const monoSamples = await resampleToMono(audioBuffer, TARGET_SAMPLE_RATE);

          // Encode as WAV and convert to base64
          const wavBytes = encodeWav(monoSamples, TARGET_SAMPLE_RATE);
          const base64Wav = uint8ToBase64(wavBytes);

          callbacks.onComplete?.(base64Wav);
        } catch (error) {
          callbacks.onError?.(new Error(`Audio encoding failed: ${error.message}`));
        }
      };

      mediaRecorder.onerror = (event) => {
        recording = false;
        callbacks.onError?.(new Error(event.error?.message || "Recording failed."));
      };

      mediaRecorder.start();
      return true;
    } catch (error) {
      recording = false;
      chunks = [];
      mediaRecorder = null;
      if (mediaStream) {
        for (const track of mediaStream.getTracks()) {
          track.stop();
        }
        mediaStream = null;
      }
      throw new Error(error.message || "Recording could not start.");
    }
  }

  function stopRecording() {
    if (!mediaRecorder || !recording) {
      return;
    }

    mediaRecorder.stop();
  }

  function cleanup() {
    if (mediaRecorder && recording) {
      mediaRecorder.stop();
    }

    if (mediaStream) {
      for (const track of mediaStream.getTracks()) {
        track.stop();
      }
      mediaStream = null;
    }

    chunks = [];
    recording = false;
  }

  return {
    startRecording,
    stopRecording,
    isRecording: () => recording,
    cleanup,
  };
}
