'use strict';

const DSD_RATE = 2822400;
const PCM_RATE = 44100;
const DECIMATION = DSD_RATE / PCM_RATE;

function packByte(bits, offset, lsbFirst = false) {
  let value = 0;
  for (let bit = 0; bit < 8; bit += 1) {
    if (!bits[offset + bit]) continue;
    value |= lsbFirst ? 1 << bit : 1 << (7 - bit);
  }
  return value;
}

function unpackByte(value, lsbFirst = false) {
  const bits = new Array(8);
  for (let bit = 0; bit < 8; bit += 1) {
    bits[bit] = lsbFirst ? (value >> bit) & 1 : (value >> (7 - bit)) & 1;
  }
  return bits;
}

function packBits(bits, lsbFirst = false) {
  const bytes = Buffer.alloc(Math.ceil(bits.length / 8));
  for (let index = 0; index < bytes.length; index += 1) {
    bytes[index] = packByte(bits, index * 8, lsbFirst);
  }
  return bytes;
}

function unpackBits(bytes, bitCount, lsbFirst = false) {
  const bits = new Uint8Array(bitCount);
  for (let index = 0; index < bitCount; index += 1) {
    const byte = bytes[Math.floor(index / 8)] || 0;
    const bit = index % 8;
    bits[index] = lsbFirst ? (byte >> bit) & 1 : (byte >> (7 - bit)) & 1;
  }
  return bits;
}

function generateFixture({ pcmSamples = 4096, channels = 2 } = {}) {
  const source = Array.from({ length: channels }, () => new Float64Array(pcmSamples));
  for (let sample = 0; sample < pcmSamples; sample += 1) {
    const time = sample / PCM_RATE;
    source[0][sample] =
      0.34 * Math.sin(2 * Math.PI * 997 * time) +
      0.08 * Math.sin(2 * Math.PI * 313 * time);
    if (channels > 1) {
      source[1][sample] =
        0.29 * Math.sin(2 * Math.PI * 523.25 * time + 0.2) +
        0.06 * Math.sin(2 * Math.PI * 173 * time);
    }
  }

  const dsdChannels = source.map((channel) => {
    const bits = new Uint8Array(pcmSamples * DECIMATION);
    let integrator = 0;
    let previous = -1;
    for (let index = 0; index < bits.length; index += 1) {
      const pcmSample = channel[Math.floor(index / DECIMATION)];
      integrator += pcmSample - previous;
      previous = integrator >= 0 ? 1 : -1;
      bits[index] = previous > 0 ? 1 : 0;
    }
    return bits;
  });

  return {
    dsdRate: DSD_RATE,
    pcmRate: PCM_RATE,
    decimation: DECIMATION,
    channels: dsdChannels,
    sourcePcm: source,
  };
}

function decodeDsdToPcm(channelBits, { decimation = DECIMATION, scale = 0.72 } = {}) {
  const sampleCount = Math.min(...channelBits.map((bits) => Math.floor(bits.length / decimation)));
  const channels = channelBits.map(() => new Int16Array(sampleCount));

  for (let sample = 0; sample < sampleCount; sample += 1) {
    for (let channel = 0; channel < channelBits.length; channel += 1) {
      let sum = 0;
      const offset = sample * decimation;
      for (let bit = 0; bit < decimation; bit += 1) {
        sum += channelBits[channel][offset + bit] ? 1 : -1;
      }
      const normalized = Math.max(-1, Math.min(1, (sum / decimation) / scale));
      channels[channel][sample] = Math.round(normalized * 32767);
    }
  }

  const pcm = Buffer.alloc(sampleCount * channelBits.length * 2);
  let offset = 0;
  for (let sample = 0; sample < sampleCount; sample += 1) {
    for (let channel = 0; channel < channelBits.length; channel += 1) {
      pcm.writeInt16LE(channels[channel][sample], offset);
      offset += 2;
    }
  }
  return { sampleCount, channels, pcm };
}

module.exports = {
  DECIMATION,
  DSD_RATE,
  PCM_RATE,
  decodeDsdToPcm,
  generateFixture,
  packBits,
  packByte,
  unpackBits,
  unpackByte,
};
