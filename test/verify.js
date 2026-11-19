// Lightweight assertion-based verification run with `node test/verify.js`.
// Cross-checks physical constants, filter fit quality, FFT/IFFT round trips,
// biquad topology against direct z-plane evaluation, and ranking logic.

import {
  airAbsorptionCoefficient,
  distanceGainDb,
  predelaySeconds,
} from "../src/physics.js";
import {
  fft,
  ifft,
  highShelf,
  biquadResponseDb,
  applyBiquad,
  weightedRmsDb,
  fftConvolve,
  makePinkNoise,
  nextPowerOfTwo,
} from "../src/dsp.js";
import {
  fitAirAbsorptionFilter,
  cascadeResponseDb,
  genericHrtfDb,
} from "../src/airFilter.js";

let failures = 0;
function check(name, condition, detail = "") {
  if (condition) {
    console.log(`PASS ${name}`);
  } else {
    failures += 1;
    console.error(`FAIL ${name} ${detail}`);
  }
}

function approx(a, b, tol) {
  return Math.abs(a - b) <= tol;
}

// 1. ISO 9613-1 reference values at 20 C, 101.325 kPa, 50 %RH.
// Published tabulated alpha (dB/m): 2 kHz ~= 0.0097, 4 kHz ~= 0.029,
// 8 kHz ~= 0.105, 16 kHz ~= 0.36.
const conditions = { temperature: 293.15, pressure: 101.325, humidity: 50 };
check(
  "ISO alpha 2 kHz near 0.0097 dB/m",
  approx(airAbsorptionCoefficient(2000, conditions), 0.0097, 0.001),
);
check(
  "ISO alpha 8 kHz near 0.105 dB/m",
  approx(airAbsorptionCoefficient(8000, conditions), 0.105, 0.006),
);
check(
  "ISO alpha 16 kHz near 0.36 dB/m",
  approx(airAbsorptionCoefficient(16000, conditions), 0.36, 0.02),
);
check(
  "alpha increases monotonically with frequency above 250 Hz",
  [500, 1000, 2000, 4000, 8000, 16000]
    .map((f) => airAbsorptionCoefficient(f, conditions))
    .every((v, i, arr) => i === 0 || v > arr[i - 1]),
);

// 2. Inverse-square law and predelay.
check("distance law 2x = -6.02 dB", approx(distanceGainDb(2), -6.0206, 0.001));
check("distance law 8x = -18.06 dB", approx(distanceGainDb(8), -18.0618, 0.001));
check(
  "predelay grows with distance",
  predelaySeconds(8) > predelaySeconds(2),
);

// 3. FFT/IFFT round trip on a mixed sinusoid.
const sr = 48000;
const n = 4096;
const re = new Float64Array(n);
const im = new Float64Array(n);
for (let i = 0; i < n; i++) {
  re[i] =
    0.7 * Math.sin((2 * Math.PI * 440 * i) / sr) +
    0.3 * Math.sin((2 * Math.PI * 1200 * i) / sr);
}
const original = Float64Array.from(re);
fft(re, im);
ifft(re, im);
let maxErr = 0;
for (let i = 0; i < n; i++) maxErr = Math.max(maxErr, Math.abs(re[i] - original[i]));
check("FFT/IFFT round trip error < 1e-10", maxErr < 1e-10, `err=${maxErr}`);

// 4. Biquad response matches direct z-plane evaluation.
function directZ(b, a, freq) {
  const w = (2 * Math.PI * freq) / sr;
  const numRe = b[0] + b[1] * Math.cos(-w) + b[2] * Math.cos(-2 * w);
  const numIm = b[1] * Math.sin(-w) + b[2] * Math.sin(-2 * w);
  const denRe = 1 + a[1] * Math.cos(-w) + a[2] * Math.cos(-2 * w);
  const denIm = a[1] * Math.sin(-w) + a[2] * Math.sin(-2 * w);
  return 20 * Math.log10(Math.hypot(numRe, numIm) / Math.hypot(denRe, denIm));
}
const shelf = highShelf(4000, -9, sr);
let responseErr = 0;
for (const freq of [60, 250, 1000, 4000, 9000, 18000]) {
  responseErr = Math.max(
    responseErr,
    Math.abs(
      biquadResponseDb(shelf.b, shelf.a, freq, sr) -
        directZ(shelf.b, shelf.a, freq),
    ),
  );
}
check("biquad response matches direct z-plane", responseErr < 1e-9);

// 5. Air absorption filter fit stays within 1 dB up to 32 m.
const evalFreqs = Array.from(
  { length: 200 },
  (_, i) => 20 * Math.pow(20000 / 20, i / 199),
);
for (const distance of [1, 8, 32]) {
  const target = evalFreqs.map(
    (f) => -airAbsorptionCoefficient(f, conditions) * distance,
  );
  const fitted = fitAirAbsorptionFilter(
    target,
    evalFreqs,
    [250, 500, 1000, 2000, 4000, 8000, 12000, 16000],
    sr,
  );
  const worst = Math.max(
    ...evalFreqs.map(
      (f, i) =>
        Math.abs(cascadeResponseDb(f, fitted, sr) - target[i]),
    ),
  );
  check(`air filter fit < 1 dB at ${distance} m`, worst < 1, `worst=${worst}`);
}

// 6. Time-domain biquad only attenuates (negative shelf gains).
const pink = makePinkNoise(sr, 3);
const cut = applyBiquad(pink, highShelf(8000, -12, sr));
let energyIn = 0;
let energyOut = 0;
for (let i = 0; i < pink.length; i++) {
  energyIn += pink[i] * pink[i];
  energyOut += cut[i] * cut[i];
}
check("negative shelf reduces signal energy", energyOut < energyIn);

// 7. Convolution with a delta impulse preserves the signal.
const delta = new Float64Array(64);
delta[0] = 1;
const convolved = fftConvolve(pink.subarray(0, 8192), delta);
let convErr = 0;
for (let i = 0; i < 8192; i++) {
  convErr = Math.max(convErr, Math.abs(convolved[i] - pink[i]));
}
check("delta convolution identity", convErr < 1e-12);

// 8. A-weighted loudness is finite and penalizes the far, dull source more.
const near = makePinkNoise(sr * 2, 11);
const far = applyBiquad(
  near.map((v) => v * 0.03),
  highShelf(6000, -18, sr),
);
const nearDb = weightedRmsDb(near, sr);
const farDb = weightedRmsDb(far, sr);
check("weighted levels finite", Number.isFinite(nearDb) && Number.isFinite(farDb));
check("far source is at least 25 dB quieter", nearDb - farDb > 25, `${nearDb - farDb}`);

// 9. Generic HRTF is zero at DC and finite across band.
check("HRTF DC = 0 dB", genericHrtfDb(0) === 0);
check(
  "HRTF finite across 20 Hz-20 kHz",
  Array.from({ length: 50 }, (_, i) =>
    Number.isFinite(genericHrtfDb(20 * Math.pow(1000, i / 49))),
  ).every(Boolean),
);

// 10. Power-of-two helper.
check("nextPowerOfTwo", nextPowerOfTwo(100000) === 131072);

console.log(failures === 0 ? "\nAll checks passed." : `\n${failures} check(s) failed.`);
process.exit(failures === 0 ? 0 : 1);
