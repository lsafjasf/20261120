import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import {
  SPEED_OF_SOUND,
  airAbsorptionCoefficient,
  distanceGainDb,
  predelaySeconds,
} from "./physics.js";
import {
  fft,
  nextPowerOfTwo,
  applyBiquad,
  fftConvolve,
  makePinkNoise,
  weightedRmsDb,
  rmsDb,
  encodeWav,
} from "./dsp.js";
import {
  fitAirAbsorptionFilter,
  cascadeResponseDb,
  genericHrtfDb,
  makeRoomImpulse,
} from "./airFilter.js";
import { lineChart } from "./plot.js";
import { toCsv } from "./csv.js";

const SAMPLE_RATE = 48000;
const DURATION_SEC = 3;
const DISTANCES = [1, 2, 4, 8, 16, 32];
const CONDITIONS = { temperature: 293.15, pressure: 101.325, humidity: 50 };
const SHELF_FREQS = [250, 500, 1000, 2000, 4000, 8000, 12000, 16000];
const RT60 = 0.9;
const BASE_PREDELAY = 0.02;
const OUT_DIR = join("artifacts", "results");

mkdirSync(OUT_DIR, { recursive: true });

// Log-spaced evaluation grid from 20 Hz to just below Nyquist.
const FREQ_COUNT = 240;
const frequencies = Array.from(
  { length: FREQ_COUNT },
  (_, i) => 20 * Math.pow(20000 / 20, i / (FREQ_COUNT - 1)),
);

// 1) Physical targets and fitted minimum-phase air absorption filters.
const perDistance = DISTANCES.map((distance) => {
  const airTarget = frequencies.map(
    (f) => -airAbsorptionCoefficient(f, CONDITIONS) * distance,
  );
  const fitted = fitAirAbsorptionFilter(
    airTarget,
    frequencies,
    SHELF_FREQS,
    SAMPLE_RATE,
  );
  const fitError = frequencies.map((f, i) => {
    const actual = cascadeResponseDb(f, fitted, SAMPLE_RATE);
    return actual - airTarget[i];
  });
  const hrtf = frequencies.map((f) => genericHrtfDb(f));
  const distanceLaw = distanceGainDb(distance);
  return {
    distance,
    airTarget,
    fitted,
    fitError,
    hrtf,
    distanceLaw,
    predelaySec: predelaySeconds(distance, BASE_PREDELAY),
  };
});

// 2) Source stimulus and measured (Welch) magnitude spectra.
const source = makePinkNoise(SAMPLE_RATE * DURATION_SEC, 20261120);

function applyAirFilter(signal, fitted) {
  let out = signal;
  for (const filter of fitted.filters) out = applyBiquad(out, filter);
  return out;
}

function applyHrtfMagnitude(signal) {
  const n = nextPowerOfTwo(signal.length);
  const re = new Float64Array(n);
  const im = new Float64Array(n);
  for (let i = 0; i < signal.length; i++) re[i] = signal[i];
  fft(re, im);
  for (let k = 0; k < n; k++) {
    const mirror = k <= n / 2 ? k : n - k;
    const freq = (mirror * SAMPLE_RATE) / n;
    const gain =
      freq === 0 ? 1 : Math.pow(10, genericHrtfDb(freq) / 20);
    re[k] *= gain;
    im[k] *= gain;
  }
  // Reuse inverse transform via conjugated forward FFT.
  for (let i = 0; i < n; i++) im[i] = -im[i];
  fft(re, im);
  return re.map((v) => v / n).slice(0, signal.length);
}

function delaySignal(signal, delaySamples) {
  const out = new Float64Array(signal.length);
  out.set(signal.subarray(0, signal.length - delaySamples), delaySamples);
  return out;
}

// Average-periodogram power spectrum, converted to dB relative to the source.
function welchDb(signal, reference) {
  const block = 16384;
  const blocks = Math.floor(signal.length / block);
  const power = new Float64Array(block);
  const refPower = new Float64Array(block);
  const re = new Float64Array(block);
  const im = new Float64Array(block);
  const reRef = new Float64Array(block);
  const imRef = new Float64Array(block);
  for (let b = 0; b < blocks; b++) {
    re.fill(0);
    im.fill(0);
    reRef.fill(0);
    imRef.fill(0);
    for (let i = 0; i < block; i++) {
      re[i] = signal[b * block + i];
      reRef[i] = reference[b * block + i];
    }
    fft(re, im);
    fft(reRef, imRef);
    for (let i = 0; i < block; i++) {
      power[i] += re[i] * re[i] + im[i] * im[i];
      refPower[i] += reRef[i] * reRef[i] + imRef[i] * imRef[i];
    }
  }
  return power.map((p, i) =>
    10 * Math.log10(Math.max(p, 1e-20) / Math.max(refPower[i], 1e-20)),
  );
}

const binFreq = (bin) => (bin * SAMPLE_RATE) / 16384;

// 3) Render each variant, measure loudness and response curves, write WAVs.
const loudnessRows = [];
const responseRows = [];

for (const item of perDistance) {
  const { distance, fitted, predelaySec } = item;
  const linearGain = Math.pow(10, item.distanceLaw / 20);

  const distanceOnly = source.map((v) => v * linearGain);
  const plusAir = applyAirFilter(distanceOnly, fitted);
  const plusHrtf = applyHrtfMagnitude(plusAir);

  const delaySamples = Math.round((distance / SPEED_OF_SOUND) * SAMPLE_RATE);
  const direct = delaySignal(plusHrtf, delaySamples);

  const impulse = makeRoomImpulse(
    SAMPLE_RATE,
    predelaySec,
    RT60,
    100 + distance,
  );
  const wet = fftConvolve(plusHrtf, impulse);
  const wetGain = 0.55;
  const mixLength = Math.max(direct.length, wet.length);
  const mix = new Float64Array(mixLength);
  for (let i = 0; i < mixLength; i++) {
    mix[i] =
      (direct[i] || 0) + wetGain * (wet[i] || 0);
  }
  let peak = 0;
  for (let i = 0; i < mix.length; i++) {
    const magnitude = Math.abs(mix[i]);
    if (magnitude > peak) peak = magnitude;
  }
  const mixNorm = mix.map((v) => (v / peak) * 0.9);

  writeFileSync(
    join(OUT_DIR, `distance_${String(distance).padStart(2, "0")}m.wav`),
    encodeWav([mixNorm], SAMPLE_RATE),
  );

  loudnessRows.push({
    distance_m: distance,
    rms_distance_only_dbfs: rmsDb(distanceOnly).toFixed(2),
    rms_plus_air_dbfs: rmsDb(plusAir).toFixed(2),
    rms_plus_air_hrtf_dbfs: rmsDb(plusHrtf).toFixed(2),
    a_weighted_full_chain_dbfs: weightedRmsDb(
      plusHrtf,
      SAMPLE_RATE,
    ).toFixed(2),
    predelay_ms: (predelaySec * 1000).toFixed(2),
  });

  const measured = welchDb(direct, source);
  for (let bin = 1; bin < 16384 / 2; bin++) {
    const f = binFreq(bin);
    if (f < 20 || f > 20000) continue;
    responseRows.push({
      distance_m: distance,
      frequency_hz: f.toFixed(1),
      total_response_db: measured[bin].toFixed(2),
      distance_law_db: item.distanceLaw.toFixed(2),
      air_absorption_target_db: item.airTarget[
        nearestIndex(frequencies, f)
      ].toFixed(2),
      air_filter_fitted_db: cascadeResponseDb(f, fitted, SAMPLE_RATE).toFixed(2),
      hrtf_db: genericHrtfDb(f).toFixed(2),
    });
  }
}

function nearestIndex(grid, value) {
  let best = 0;
  let bestDelta = Infinity;
  for (let i = 0; i < grid.length; i++) {
    const delta = Math.abs(Math.log2(grid[i] / value));
    if (delta < bestDelta) {
      bestDelta = delta;
      best = i;
    }
  }
  return best;
}

// 4) Air absorption coefficient table versus frequency (ISO 9613-1).
const tableFrequencies = [
  31.5, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 12500, 16000,
];
const coefficientRows = tableFrequencies.map((f) => {
  const alpha = airAbsorptionCoefficient(f, CONDITIONS);
  return {
    frequency_hz: f,
    alpha_db_per_m: alpha.toExponential(4),
    loss_at_1m_db: (alpha * 1).toFixed(3),
    loss_at_8m_db: (alpha * 8).toFixed(3),
    loss_at_32m_db: (alpha * 32).toFixed(3),
  };
});
writeFileSync(
  join(OUT_DIR, "air_absorption_coefficients.csv"),
  toCsv(coefficientRows),
);
writeFileSync(join(OUT_DIR, "frequency_response.csv"), toCsv(responseRows));
writeFileSync(join(OUT_DIR, "loudness_comparison.csv"), toCsv(loudnessRows));

// 5) Loudness deltas relative to the 1 m reference.
const reference = loudnessRows[0];
const loudnessDeltaRows = loudnessRows.slice(1).map((row) => ({
  distance_m: row.distance_m,
  rms_delta_distance_only_db: (
    Number(row.rms_distance_only_dbfs) -
    Number(reference.rms_distance_only_dbfs)
  ).toFixed(2),
  rms_delta_plus_air_db: (
    Number(row.rms_plus_air_dbfs) -
    Number(reference.rms_plus_air_dbfs)
  ).toFixed(2),
  a_weighted_delta_full_chain_db: (
    Number(row.a_weighted_full_chain_dbfs) -
    Number(reference.a_weighted_full_chain_dbfs)
  ).toFixed(2),
  theoretical_distance_law_db: distanceGainDb(
    Number(row.distance_m),
  ).toFixed(2),
}));
writeFileSync(
  join(OUT_DIR, "loudness_deltas.csv"),
  toCsv(loudnessDeltaRows),
);

// 6) SVG plots.
const responseSeries = perDistance.map((item) => ({
  name: `${item.distance} m`,
  y: frequencies.map((f) => {
    const idx = nearestIndex(frequencies, f);
    return (
      item.distanceLaw +
      cascadeResponseDb(f, item.fitted, SAMPLE_RATE) +
      item.hrtf[idx]
    );
  }),
}));
writeFileSync(
  join(OUT_DIR, "frequency_response_curves.svg"),
  lineChart({
    xValues: frequencies,
    series: responseSeries,
    title: "Total response vs distance (distance law + air absorption + HRTF)",
    yLabel: "Magnitude relative to source (dB)",
    yDomain: [-75, 8],
  }),
);

const at8m = perDistance.find((d) => d.distance === 8);
const componentSeries = [
  {
    name: "Inverse-square law (8 m)",
    dashed: true,
    y: frequencies.map(() => distanceGainDb(8)),
  },
  { name: "Air absorption target (8 m)", y: at8m.airTarget },
  {
    name: "Fitted biquad cascade (8 m)",
    dashed: true,
    y: frequencies.map((f) =>
      cascadeResponseDb(f, at8m.fitted, SAMPLE_RATE),
    ),
  },
  {
    name: "Generic HRTF magnitude",
    y: frequencies.map((f) => genericHrtfDb(f)),
  },
];
writeFileSync(
  join(OUT_DIR, "response_components_8m.svg"),
  lineChart({
    xValues: frequencies,
    series: componentSeries,
    title: "Response components at 8 m (ISO 9613-1 fit quality and HRTF)",
    yLabel: "Magnitude (dB)",
    yDomain: [-25, 5],
  }),
);

const airCurveSeries = DISTANCES.map((distance) => ({
  name: `${distance} m`,
  y: frequencies.map(
    (f) => -airAbsorptionCoefficient(f, CONDITIONS) * distance,
  ),
}));
writeFileSync(
  join(OUT_DIR, "air_absorption_curves.svg"),
  lineChart({
    xValues: frequencies,
    series: airCurveSeries,
    title: "ISO 9613-1 air absorption loss vs frequency and distance",
    yLabel: "Air loss (dB)",
    yDomain: [-30, 1],
  }),
);

// 7) Fit-quality summary and predelay table.
const fitSummary = perDistance.map((item) => {
  const absErrors = item.fitError.map(Math.abs);
  return {
    distance_m: item.distance,
    max_abs_fit_error_db: Math.max(...absErrors).toFixed(3),
    mean_abs_fit_error_db: (
      absErrors.reduce((a, b) => a + b, 0) / absErrors.length
    ).toFixed(3),
    predelay_ms: (item.predelaySec * 1000).toFixed(2),
  };
});
writeFileSync(join(OUT_DIR, "filter_fit_summary.csv"), toCsv(fitSummary));

writeFileSync(
  join(OUT_DIR, "summary.json"),
  JSON.stringify(
    {
      conditions: CONDITIONS,
      sampleRate: SAMPLE_RATE,
      distances: DISTANCES,
      shelfFrequencies: SHELF_FREQS,
      speedOfSound: SPEED_OF_SOUND,
      rt60Seconds: RT60,
      loudnessRows,
      loudnessDeltaRows,
      fitSummary,
    },
    null,
    2,
  ),
);

console.log("Experiment complete. Artifacts in", OUT_DIR);
for (const row of loudnessDeltaRows) console.log(row);
for (const row of fitSummary) console.log(row);
