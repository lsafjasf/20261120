export const MU_BIAS = 0x84;
export const MU_CLIP = 32635;
export const A_CLIP = 32635;

export function muEncodeReference(sample) {
  let sign = 0;
  let magnitude = sample;
  if (magnitude < 0) {
    sign = 0x80;
    magnitude = -magnitude;
  }
  if (magnitude > MU_CLIP) magnitude = MU_CLIP;

  magnitude += MU_BIAS;
  const exponent = segmentForBias(magnitude);
  const mantissa = (magnitude >> (exponent + 3)) & 0x0f;
  return ~(sign | (exponent << 4) | mantissa) & 0xff;
}

export function muDecodeReference(code) {
  const value = ~code & 0xff;
  const exponent = (value >> 4) & 0x07;
  const mantissa = value & 0x0f;
  const magnitude =
    (((mantissa << 3) + MU_BIAS) << exponent) - MU_BIAS;
  return value & 0x80 ? -magnitude : magnitude;
}

export function aEncodeReference(sample) {
  const sign = sample >= 0 ? 0x80 : 0x00;
  let magnitude = Math.abs(sample);
  if (magnitude > A_CLIP) magnitude = A_CLIP;

  let exponent = 0;
  let mantissa = 0;
  if (magnitude < 256) {
    mantissa = (magnitude >> 4) & 0x0f;
  } else {
    exponent = segmentForBias(magnitude);
    mantissa = (magnitude >> (exponent + 3)) & 0x0f;
  }
  return ((sign | (exponent << 4) | mantissa) ^ 0x55) & 0xff;
}

export function aDecodeReference(code) {
  const value = (code ^ 0x55) & 0xff;
  const exponent = (value >> 4) & 0x07;
  const mantissa = value & 0x0f;
  const magnitude =
    exponent === 0
      ? (mantissa * 2 + 1) << 3
      : (mantissa * 2 + 1 + 32) << (exponent + 2);
  return value & 0x80 ? magnitude : -magnitude;
}

function segmentForBias(value) {
  if (value < 256) return 0;
  if (value < 512) return 1;
  if (value < 1024) return 2;
  if (value < 2048) return 3;
  if (value < 4096) return 4;
  if (value < 8192) return 5;
  if (value < 16384) return 6;
  return 7;
}

export const codecs = {
  mulaw: {
    name: "mu-law",
    encode: muEncodeReference,
    decode: muDecodeReference,
  },
  alaw: {
    name: "A-law",
    encode: aEncodeReference,
    decode: aDecodeReference,
  },
};

export function loopbackReference(sample, codec) {
  const rule = codecs[codec];
  return rule.decode(rule.encode(sample));
}
