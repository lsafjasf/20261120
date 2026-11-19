// Dependency-free DSP building blocks shared by offline verification and the
// Web Audio demo (same math as the browser BiquadFilter/Convolver nodes).

// Iterative radix-2 FFT. Length must be a power of two.
export function fft(re, im) {
  const n = re.length;
  for (let i = 1, j = 0; i < n; i++) {
    let bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) {
      [re[i], re[j]] = [re[j], re[i]];
      [im[i], im[j]] = [im[j], im[i]];
    }
  }
  for (let len = 2; len <= n; len <<= 1) {
    const angle = (-2 * Math.PI) / len;
    const wRe = Math.cos(angle);
    const wIm = Math.sin(angle);
    for (let i = 0; i < n; i += len) {
      let curRe = 1;
      let curIm = 0;
      const half = len >> 1;
      for (let k = 0; k < half; k++) {
        const uRe = re[i + k];
        const uIm = im[i + k];
        const vRe = curRe * re[i + k + half] - curIm * im[i + k + half];
        const vIm = curRe * im[i + k + half] + curIm * re[i + k + half];
        re[i + k] = uRe + vRe;
        im[i + k] = uIm + vIm;
        re[i + k + half] = uRe - vRe;
        im[i + k + half] = uIm - vIm;
        const nextRe = curRe * wRe - curIm * wIm;
        curIm = curRe * wIm + curIm * wRe;
        curRe = nextRe;
      }
    }
  }
}

export function ifft(re, im) {
  const n = re.length;
  for (let i = 0; i < n; i++) im[i] = -im[i];
  fft(re, im);
  for (let i = 0; i < n; i++) {
    re[i] /= n;
    im[i] = -im[i] / n;
  }
}

// Magnitude response of a normalized DF2-T biquad (RBJ form), in dB.
export function biquadResponseDb(b, a, frequency, sampleRate) {
  const w = (2 * Math.PI * frequency) / sampleRate;
  const c = Math.cos(w);
  const num =
    b[0] * b[0] +
    b[1] * b[1] +
    b[2] * b[2] +
    2 * (b[0] * b[1] + b[1] * b[2]) * c +
    2 * b[0] * b[2] * Math.cos(2 * w);
  const den =
    1 +
    a[1] * a[1] +
    a[2] * a[2] +
    2 * (a[1] + a[1] * a[2]) * c +
    2 * a[2] * Math.cos(2 * w);
  return 10 * Math.log10(Math.max(num / den, 1e-20));
}

// RBJ high-shelf coefficients, gain in dB.
export function highShelf(frequency, gainDb, sampleRate) {
  const w = (2 * Math.PI * frequency) / sampleRate;
  const A = Math.pow(10, gainDb / 40);
  const cos = Math.cos(w);
  const sin = Math.sin(w);
  const S = 1;
  const alpha = (sin / 2) * Math.sqrt((A + 1 / A) * (1 / S - 1) + 2);
  const twoSqrtAAlpha = 2 * Math.sqrt(A) * alpha;
  const Ap1 = A + 1;
  const Am1 = A - 1;
  const b = [
    A * (Ap1 + Am1 * cos + twoSqrtAAlpha),
    -2 * A * (Am1 + Ap1 * cos),
    A * (Ap1 + Am1 * cos - twoSqrtAAlpha),
  ];
  const a = [
    Ap1 - Am1 * cos + twoSqrtAAlpha,
    2 * (Am1 - Ap1 * cos),
    Ap1 - Am1 * cos - twoSqrtAAlpha,
  ];
  return normalizeBiquad(b, a);
}

export function lowpass(frequency, q, sampleRate) {
  const w = (2 * Math.PI * frequency) / sampleRate;
  const alpha = Math.sin(w) / (2 * q);
  const cos = Math.cos(w);
  const b = [(1 - cos) / 2, 1 - cos, (1 - cos) / 2];
  const a = [1 + alpha, -2 * cos, 1 - alpha];
  return normalizeBiquad(b, a);
}

function normalizeBiquad(b, a) {
  return { b: b.map((v) => v / a[0]), a: a.map((v) => v / a[0]) };
}

// Apply a biquad to a full buffer using the transposed Direct Form II topology
// that the Web Audio BiquadFilterNode uses.
export function applyBiquad(input, { b, a }) {
  const out = new Float64Array(input.length);
  let z1 = 0;
  let z2 = 0;
  for (let i = 0; i < input.length; i++) {
    const x = input[i];
    const y = b[0] * x + z1;
    z1 = b[1] * x - a[1] * y + z2;
    z2 = b[2] * x - a[2] * y;
    out[i] = y;
  }
  return out;
}

// Solve A x = b by Gauss-Jordan elimination with partial pivoting.
// A is row-major m x n; returns x (m >= n, least-squares is achieved by
// passing the normal equations from fitShelfGains).
export function solveLinear(matrix, rhs) {
  const a = matrix.map((row, i) => [...row, rhs[i]]);
  const rows = a.length;
  const cols = a[0].length - 1;
  for (let col = 0; col < cols; col++) {
    let pivot = col;
    for (let r = col + 1; r < rows; r++) {
      if (Math.abs(a[r][col]) > Math.abs(a[pivot][col])) pivot = r;
    }
    [a[col], a[pivot]] = [a[pivot], a[col]];
    const divisor = a[col][col] || 1e-18;
    for (let c = col; c <= cols; c++) a[col][c] /= divisor;
    for (let r = 0; r < rows; r++) {
      if (r === col) continue;
      const factor = a[r][col];
      if (factor === 0) continue;
      for (let c = col; c <= cols; c++) a[r][c] -= factor * a[col][c];
    }
  }
  return Array.from({ length: cols }, (_, i) => a[i][cols]);
}

// IEC 61672-1 A-weighting magnitude in dB (0 dB at 1 kHz by construction).
export function aWeightingDb(frequency) {
  const f = frequency;
  const f2 = f * f;
  const ra =
    (12194 * 12194 * f2 * f2) /
    ((f2 + 20.6 * 20.6) *
      Math.sqrt((f2 + 107.7 * 107.7) * (f2 + 737.9 * 737.9)) *
      (f2 + 12194 * 12194));
  return 20 * Math.log10(ra) + 2.0;
}

export function nextPowerOfTwo(n) {
  return 1 << Math.ceil(Math.log2(n));
}

// Linear (no overlap) FFT convolution of a signal with an impulse response.
export function fftConvolve(signal, impulse) {
  const n = nextPowerOfTwo(signal.length + impulse.length - 1);
  const re = new Float64Array(n);
  const im = new Float64Array(n);
  const irRe = new Float64Array(n);
  const irIm = new Float64Array(n);
  for (let i = 0; i < signal.length; i++) re[i] = signal[i];
  for (let i = 0; i < impulse.length; i++) irRe[i] = impulse[i];
  fft(re, im);
  fft(irRe, irIm);
  for (let i = 0; i < n; i++) {
    const r = re[i] * irRe[i] - im[i] * irIm[i];
    im[i] = re[i] * irIm[i] + im[i] * irRe[i];
    re[i] = r;
  }
  ifft(re, im);
  return re.slice(0, signal.length + impulse.length - 1);
}

// Deterministic pink-ish noise: summed octave-spaced white noise streams
// (Paul Kellett's approximate 1/f filter), seeded so runs are reproducible.
export function makePinkNoise(length, seed = 42) {
  let state = seed >>> 0;
  const rand = () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return state / 4294967296 * 2 - 1;
  };
  const out = new Float64Array(length);
  let b0 = 0, b1 = 0, b2 = 0, b3 = 0, b4 = 0, b5 = 0, b6 = 0;
  for (let i = 0; i < length; i++) {
    const white = rand();
    b0 = 0.99886 * b0 + white * 0.0555179;
    b1 = 0.99332 * b1 + white * 0.0750759;
    b2 = 0.969 * b2 + white * 0.153852;
    b3 = 0.8665 * b3 + white * 0.3104856;
    b4 = 0.55 * b4 + white * 0.5329522;
    b5 = -0.7616 * b5 - white * 0.016898;
    out[i] = (b0 + b1 + b2 + b3 + b4 + b5 + b6 + white * 0.5362) * 0.11;
    b6 = white * 0.115926;
  }
  return out;
}

// Root-mean-square level of a buffer in dBFS (full scale = 1).
export function rmsDb(buffer) {
  let sum = 0;
  for (let i = 0; i < buffer.length; i++) sum += buffer[i] * buffer[i];
  return 10 * Math.log10(Math.max(sum / buffer.length, 1e-20));
}

// A-weighted RMS level: apply the weighting curve as an FFT-domain filter.
export function weightedRmsDb(buffer, sampleRate) {
  const n = nextPowerOfTwo(buffer.length * 2);
  const re = new Float64Array(n);
  const im = new Float64Array(n);
  re.set(buffer);
  fft(re, im);
  for (let k = 0; k < n; k++) {
    const mirror = k <= n / 2 ? k : n - k;
    const freq = (mirror * sampleRate) / n;
    const gain = Math.pow(10, aWeightingDb(freq) / 20);
    re[k] *= gain;
    im[k] *= gain;
  }
  const timeRe = new Float64Array(re);
  const timeIm = new Float64Array(im);
  ifft(timeRe, timeIm);
  return rmsDb(timeRe.slice(0, buffer.length));
}

// Encode interleaved mono/stereo Float64 samples to 16-bit PCM WAV bytes.
export function encodeWav(channels, sampleRate) {
  const frames = channels[0].length;
  const channelsCount = channels.length;
  const dataBytes = frames * channelsCount * 2;
  const buffer = new ArrayBuffer(44 + dataBytes);
  const view = new DataView(buffer);
  const writeString = (offset, text) => {
    for (let i = 0; i < text.length; i++)
      view.setUint8(offset + i, text.charCodeAt(i));
  };
  writeString(0, "RIFF");
  view.setUint32(4, 36 + dataBytes, true);
  writeString(8, "WAVE");
  writeString(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, channelsCount, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * channelsCount * 2, true);
  view.setUint16(32, channelsCount * 2, true);
  view.setUint16(34, 16, true);
  writeString(36, "data");
  view.setUint32(40, dataBytes, true);
  let offset = 44;
  for (let i = 0; i < frames; i++) {
    for (let ch = 0; ch < channelsCount; ch++) {
      const clipped = Math.max(-1, Math.min(1, channels[ch][i]));
      view.setInt16(offset, clipped * 32767, true);
      offset += 2;
    }
  }
  return Buffer.from(buffer);
}
