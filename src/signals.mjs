import path from "node:path";
import { fileURLToPath } from "node:url";
import { readPcm16Wav } from "./wav.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const SAMPLE_RATE = 8000;

export function silence(durationSeconds = 0.05) {
  return new Int16Array(Math.round(SAMPLE_RATE * durationSeconds));
}

export function sine({ hertz = 1000, amplitude = 16384, durationSeconds = 1 } = {}) {
  const length = Math.round(SAMPLE_RATE * durationSeconds);
  return Int16Array.from(
    { length },
    (_, index) =>
      Math.round(
        amplitude * Math.sin((2 * Math.PI * hertz * index) / SAMPLE_RATE),
      ),
  );
}

export async function speechFixture() {
  return readPcm16Wav(
    path.join(root, "fixtures", "speech_0_jackson_0.wav"),
  );
}

export async function loadSignalSets() {
  const speech = await speechFixture();
  if (speech.sampleRate !== SAMPLE_RATE) {
    throw new Error(`speech fixture must be ${SAMPLE_RATE} Hz`);
  }
  return {
    silence: { samples: silence(), description: "50 ms of digital silence" },
    sine: {
      samples: sine({ hertz: 1000, amplitude: 16384, durationSeconds: 1 }),
      description: "1 kHz sine, 50% full scale, 1 second",
    },
    speech: {
      samples: speech.samples,
      description: "FSDD 0_jackson_0.wav, real speech",
      sampleRate: speech.sampleRate,
    },
    sineSmall: {
      samples: sine({ hertz: 1000, amplitude: 32, durationSeconds: 1 }),
      description: "1 kHz sine, amplitude 32 LSB, 1 second",
    },
  };
}
