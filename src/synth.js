const PARTIALS = [
  { ratio: 1, gain: 1, decay: 0.72 },
  { ratio: 2.002, gain: 0.36, decay: 1.08 },
  { ratio: 3.008, gain: 0.18, decay: 1.55 },
  { ratio: 4.997, gain: 0.1, decay: 2.1 },
  { ratio: 7.01, gain: 0.045, decay: 3.1 },
];

function midiFrequency(pitch) {
  return 440 * 2 ** ((pitch - 69) / 12);
}

function envelope(time, noteDuration, releaseDuration) {
  const attack = Math.min(0.009, noteDuration * 0.2);
  if (time < attack) return time / Math.max(attack, 0.0001);
  const releaseStart = noteDuration;
  if (time > releaseStart) {
    const releaseTime = time - releaseStart;
    if (releaseTime >= releaseDuration) return 0;
    return Math.exp(-releaseTime * 7.5);
  }
  return 1;
}

export function synthesizePcm(analysis, options = {}) {
  const sampleRate = options.sampleRate ?? 32000;
  const maxSeconds = options.maxSeconds ?? 180;
  const tailSeconds = 1.1;
  const duration = Math.min(
    maxSeconds,
    Math.max(0.25, analysis.durationSeconds + tailSeconds),
  );
  const frameCount = Math.max(1, Math.ceil(duration * sampleRate));
  const left = new Float32Array(frameCount);
  const right = new Float32Array(frameCount);

  for (const note of analysis.notes) {
    if (note.startSeconds >= duration) continue;
    const frequency = midiFrequency(note.pitch);
    if (frequency >= sampleRate * 0.46) continue;
    const soundingEnd = note.sustainEndSeconds ?? note.endSeconds;
    const noteDuration = Math.max(0.012, soundingEnd - note.startSeconds);
    const releaseDuration = 0.52;
    const startFrame = Math.max(0, Math.floor(note.startSeconds * sampleRate));
    const endFrame = Math.min(
      frameCount,
      Math.ceil((note.startSeconds + noteDuration + releaseDuration) * sampleRate),
    );
    const pan = Math.max(-0.42, Math.min(0.42, (note.pitch - 60) / 42));
    const leftGain = Math.sqrt((1 - pan) / 2);
    const rightGain = Math.sqrt((1 + pan) / 2);
    const gain = 0.105 * (0.28 + (note.velocity / 127) * 0.72);
    const phases = PARTIALS.map((partial, index) => (index * 1.87) % (Math.PI * 2));
    const angular = PARTIALS.map((partial) => 2 * Math.PI * frequency * partial.ratio);

    for (let frame = startFrame; frame < endFrame; frame += 1) {
      const time = frame / sampleRate - note.startSeconds;
      if (time < 0) continue;
      const attackEnvelope = envelope(time, noteDuration, releaseDuration);
      if (attackEnvelope <= 0) continue;
      let sample = 0;
      for (let index = 0; index < PARTIALS.length; index += 1) {
        const partial = PARTIALS[index];
        const partialDecay = Math.exp(-time * partial.decay);
        sample +=
          Math.sin(angular[index] * time + phases[index]) *
          partial.gain *
          partialDecay;
      }
      sample *= gain * attackEnvelope;
      left[frame] += sample * leftGain;
      right[frame] += sample * rightGain;
    }
  }

  for (let frame = 0; frame < frameCount; frame += 1) {
    left[frame] = Math.tanh(left[frame] * 1.1) * 0.92;
    right[frame] = Math.tanh(right[frame] * 1.1) * 0.92;
  }
  return { left, right, sampleRate, duration };
}

function writeString(view, offset, value) {
  for (let index = 0; index < value.length; index += 1) {
    view.setUint8(offset + index, value.charCodeAt(index));
  }
}

export function encodeWav({ left, right, sampleRate }) {
  const channels = 2;
  const bytesPerSample = 2;
  const dataLength = left.length * channels * bytesPerSample;
  const buffer = new ArrayBuffer(44 + dataLength);
  const view = new DataView(buffer);

  writeString(view, 0, "RIFF");
  view.setUint32(4, 36 + dataLength, true);
  writeString(view, 8, "WAVE");
  writeString(view, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, channels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * channels * bytesPerSample, true);
  view.setUint16(32, channels * bytesPerSample, true);
  view.setUint16(34, bytesPerSample * 8, true);
  writeString(view, 36, "data");
  view.setUint32(40, dataLength, true);

  let offset = 44;
  for (let frame = 0; frame < left.length; frame += 1) {
    const leftValue = Math.max(-1, Math.min(1, left[frame]));
    const rightValue = Math.max(-1, Math.min(1, right[frame]));
    view.setInt16(offset, leftValue < 0 ? leftValue * 0x8000 : leftValue * 0x7fff, true);
    view.setInt16(offset + 2, rightValue < 0 ? rightValue * 0x8000 : rightValue * 0x7fff, true);
    offset += 4;
  }
  return new Uint8Array(buffer);
}

export function synthesizeWav(analysis, options = {}) {
  return encodeWav(synthesizePcm(analysis, options));
}
