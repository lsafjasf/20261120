import {
  highShelf,
  biquadResponseDb,
  solveLinear,
} from "./dsp.js";

// Fit a cascade of high-shelf biquads so their combined dB response matches
// the ISO 9613-1 air-loss target at every evaluation frequency. Shelf dB
// responses are nearly linear in their gain setting, so a single linear
// least-squares pass (normal equations) gives an accurate minimum-phase fit.
export function fitAirAbsorptionFilter(
  targetDb,
  evalFrequencies,
  shelfFrequencies,
  sampleRate,
) {
  const basis = 6; // basis shelves are tuned to +6 dB
  const basisResponses = shelfFrequencies.map((f0) => {
    const filter = highShelf(f0, basis, sampleRate);
    return evalFrequencies.map((f) =>
      biquadResponseDb(filter.b, filter.a, f, sampleRate),
    );
  });
  // Normal equations: (M^T M) g = M^T y, where M rows = basis responses / basis.
  const m = evalFrequencies.length;
  const k = shelfFrequencies.length;
  const normal = Array.from({ length: k }, () => new Array(k).fill(0));
  const rhs = new Array(k).fill(0);
  for (let i = 0; i < m; i++) {
    const row = basisResponses.map((col) => col[i] / basis);
    for (let p = 0; p < k; p++) {
      rhs[p] += row[p] * targetDb[i];
      for (let q = 0; q < k; q++) normal[p][q] += row[p] * row[q];
    }
  }
  const gains = solveLinear(normal, rhs).map((g) => Math.min(0, g));
  const filters = shelfFrequencies.map((f0, idx) =>
    highShelf(f0, gains[idx], sampleRate),
  );
  return { filters, gains, shelfFrequencies };
}

export function cascadeResponseDb(
  frequency,
  { filters },
  sampleRate,
) {
  let total = 0;
  for (const filter of filters) {
    total += biquadResponseDb(filter.b, filter.a, frequency, sampleRate);
  }
  return total;
}

// Generic free-field frontal HRTF magnitude estimate (no measured HRIR set is
// available offline): a broad pinna gain band near 2.7 kHz and a notch near
// 9 kHz, both modelled as Gaussian bumps on a log-frequency axis. The browser
// demo uses the measured HRTF data baked into the Web Audio PannerNode.
export function genericHrtfDb(frequency) {
  if (frequency <= 0) return 0;
  const x = Math.log2(frequency / 1000);
  const pinnaGain = 1.4 * Math.exp(-((x - 1.43) ** 2) / (2 * 0.55 ** 2));
  const notch = -2.6 * Math.exp(-((x - 3.17) ** 2) / (2 * 0.32 ** 2));
  const rolloff = -1.2 * Math.exp(
    -((x - 4.1) ** 2) / (2 * 0.7 ** 2),
  );
  return pinnaGain + notch + rolloff;
}

// Synthetic exponential-decay room impulse response with an explicit
// predelay gap (propagation delay plus the room's base predelay).
export function makeRoomImpulse(
  sampleRate,
  predelaySec,
  rt60Sec,
  seed = 7,
) {
  const predelay = Math.max(1, Math.round(predelaySec * sampleRate));
  const tail = Math.ceil(rt60Sec * sampleRate);
  const length = predelay + tail;
  const ir = new Float64Array(length);
  let state = seed >>> 0;
  const rand = () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return state / 4294967296 * 2 - 1;
  };
  const tau = rt60Sec / Math.log(1000); // amplitude time constant
  for (let i = 0; i < tail; i++) {
    const t = i / sampleRate;
    // A few strong early reflections followed by the stochastic late field.
    const early =
      i === 0 ? 1.0 :
      i === Math.round(0.012 * sampleRate) ? 0.55 :
      i === Math.round(0.027 * sampleRate) ? 0.4 :
      i === Math.round(0.045 * sampleRate) ? 0.3 : 0;
    ir[predelay + i] =
      (early + 0.22 * rand()) * Math.exp(-t / tau);
  }
  return ir;
}
