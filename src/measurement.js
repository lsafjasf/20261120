import { getFFTPlan, nextPowerOfTwo } from './fft.js';

export function createRandom(seed = 0x12345678) {
  let state = seed >>> 0;
  return () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return state / 0x100000000;
  };
}

export function generateDecayingImpulseResponse(length, sampleRate, seed = 0x51a7) {
  const random = createRandom(seed);
  const ir = new Float64Array(length);
  const decay = sampleRate * 0.28;
  for (let i = 0; i < length; i++) {
    const noise = random() * 2 - 1;
    ir[i] = noise * Math.exp(-i / decay);
  }
  ir[0] = 1;
  let peak = 0;
  for (let i = 0; i < length; i++) peak = Math.max(peak, Math.abs(ir[i]));
  for (let i = 0; i < length; i++) ir[i] /= peak;
  return ir;
}

export function generateLinearPhaseImpulseResponse(length, sampleRate, cutoffRatio = 0.998) {
  const ir = new Float64Array(length);
  const center = (length - 1) / 2;
  const normalizedCutoff = cutoffRatio * 0.5;

  for (let i = 0; i < length; i++) {
    const x = i - center;
    const sinc = x === 0
      ? 2 * normalizedCutoff
      : Math.sin(2 * Math.PI * normalizedCutoff * x) / (Math.PI * x);
    const windowPosition = (2 * Math.PI * i) / (length - 1);
    const window = 0.42 - 0.5 * Math.cos(windowPosition)
      + 0.08 * Math.cos(2 * windowPosition);
    ir[i] = sinc * window;
  }

  let peak = 0;
  for (let i = 0; i < length; i++) peak = Math.max(peak, Math.abs(ir[i]));
  for (let i = 0; i < length; i++) ir[i] /= peak;
  return ir;
}

export function generateWhiteNoise(length, seed = 0xdecafbad) {
  const random = createRandom(seed);
  const signal = new Float64Array(length);
  for (let i = 0; i < length; i++) signal[i] = random() * 2 - 1;
  return signal;
}

export function fftConvolve(signal, impulseResponse) {
  const outputLength = signal.length + impulseResponse.length - 1;
  const fftSize = nextPowerOfTwo(outputLength);
  const plan = getFFTPlan(fftSize);
  const signalReal = new Float64Array(fftSize);
  const signalImag = new Float64Array(fftSize);
  const irReal = new Float64Array(fftSize);
  const irImag = new Float64Array(fftSize);

  signalReal.set(signal);
  irReal.set(impulseResponse);
  plan.transform(signalReal, signalImag, false);
  plan.transform(irReal, irImag, false);
  for (let i = 0; i < fftSize; i++) {
    const real = signalReal[i] * irReal[i] - signalImag[i] * irImag[i];
    const imag = signalReal[i] * irImag[i] + signalImag[i] * irReal[i];
    signalReal[i] = real;
    signalImag[i] = imag;
  }
  plan.transform(signalReal, signalImag, true);
  return signalReal.subarray(0, outputLength);
}

export function runConvolver(convolver, input, options = {}) {
  const blockSize = convolver.blockSize;
  const blockCount = Math.ceil(input.length / blockSize);
  const output = new Float64Array(blockCount * blockSize);
  const blockTimesUs = [];
  const block = new Float64Array(blockSize);
  const now = () => performance.now();

  for (let b = 0; b < blockCount; b++) {
    const start = b * blockSize;
    block.fill(0);
    const available = Math.min(blockSize, input.length - start);
    for (let i = 0; i < available; i++) block[i] = input[start + i];

    const begin = options.measure ? now() : 0;
    const result = convolver.processBlock(block);
    const end = options.measure ? now() : 0;
    if (options.measure && b >= (options.warmupBlocks ?? 0)) {
      blockTimesUs.push((end - begin) * 1000);
    }
    output.set(result, start);
  }

  return { output, blockTimesUs };
}

export function percentile(values, fraction) {
  if (values.length === 0) return 0;
  const sorted = Float64Array.from(values).sort();
  const index = Math.min(sorted.length - 1, Math.max(0, Math.floor(fraction * (sorted.length - 1))));
  return sorted[index];
}

export function timeStatistics(blockTimesUs, sampleRate, blockSize) {
  const count = blockTimesUs.length;
  const budgetUs = (blockSize / sampleRate) * 1e6;
  let sum = 0;
  let max = 0;
  for (const value of blockTimesUs) {
    sum += value;
    max = Math.max(max, value);
  }
  const mean = count ? sum / count : 0;
  const variance = count
    ? blockTimesUs.reduce((acc, value) => acc + (value - mean) ** 2, 0) / count
    : 0;
  return {
    blocks: count,
    blockBudgetUs: budgetUs,
    meanUs: mean,
    medianUs: percentile(blockTimesUs, 0.5),
    p95Us: percentile(blockTimesUs, 0.95),
    p99Us: percentile(blockTimesUs, 0.99),
    maxUs: max,
    stddevUs: Math.sqrt(variance),
    cpuPercentMean: (mean / budgetUs) * 100,
    cpuPercentP95: (percentile(blockTimesUs, 0.95) / budgetUs) * 100,
    cpuPercentP99: (percentile(blockTimesUs, 0.99) / budgetUs) * 100,
    cpuPercentMax: (max / budgetUs) * 100,
    realtimeFactor: mean > 0 ? budgetUs / mean : 0,
  };
}

export function rootMeanSquare(values) {
  if (values.length === 0) return 0;
  let sum = 0;
  for (const value of values) sum += value * value;
  return Math.sqrt(sum / values.length);
}

export function differenceStatistics(first, second, blockSize) {
  const length = Math.min(first.length, second.length);
  const difference = new Float64Array(length);
  let signalEnergy = 0;
  let maxAbs = 0;
  for (let i = 0; i < length; i++) {
    const delta = first[i] - second[i];
    difference[i] = delta;
    signalEnergy += first[i] * first[i];
    maxAbs = Math.max(maxAbs, Math.abs(delta));
  }
  const rms = rootMeanSquare(difference.subarray(0, length));
  const signalRms = Math.sqrt(signalEnergy / Math.max(1, length));
  return {
    samples: length,
    maxAbs,
    rms,
    signalRms,
    snrDb: rms > 0 ? 20 * Math.log10(signalRms / rms) : Infinity,
    boundary: analyseBoundaryResidual(difference, blockSize),
  };
}

export function analyseBoundaryResidual(residual, blockSize, margin = 2) {
  const boundaryValues = [];
  const interiorValues = [];
  for (let i = blockSize; i < residual.length; i++) {
    const local = i % blockSize;
    if (local < margin || local >= blockSize - margin) {
      boundaryValues.push(residual[i]);
    } else {
      interiorValues.push(residual[i]);
    }
  }
  const boundaryRms = rootMeanSquare(boundaryValues);
  const interiorRms = rootMeanSquare(interiorValues);
  const fftSize = nextPowerOfTwo(residual.length);
  const plan = getFFTPlan(fftSize);
  const real = new Float64Array(fftSize);
  const imag = new Float64Array(fftSize);
  real.set(residual.subarray(0, Math.min(residual.length, fftSize)));
  plan.transform(real, imag, false);

  const harmonicSpacing = fftSize / blockSize;
  const combPowers = [];
  const neighbourPowers = [];
  for (let bin = harmonicSpacing; bin < fftSize / 2; bin += harmonicSpacing) {
    combPowers.push(real[bin] ** 2 + imag[bin] ** 2);
    for (let offset = 2; offset <= 5; offset++) {
      const left = bin - offset;
      const right = bin + offset;
      neighbourPowers.push(real[left] ** 2 + imag[left] ** 2);
      neighbourPowers.push(real[right] ** 2 + imag[right] ** 2);
    }
  }
  const combMean = combPowers.reduce((sum, value) => sum + value, 0)
    / Math.max(1, combPowers.length);
  const neighbourMean = neighbourPowers.reduce((sum, value) => sum + value, 0)
    / Math.max(1, neighbourPowers.length);
  return {
    boundaryRms,
    interiorRms,
    boundaryToInteriorDb: interiorRms > 0
      ? 20 * Math.log10(boundaryRms / interiorRms)
      : -Infinity,
    blockRateCombDb: neighbourMean > 0 && combMean > 0
      ? 10 * Math.log10(combMean / neighbourMean)
      : -Infinity,
  };
}

function unwrapPhase(phase) {
  const unwrapped = new Float64Array(phase.length);
  if (phase.length === 0) return unwrapped;
  unwrapped[0] = phase[0];
  let offset = 0;
  for (let i = 1; i < phase.length; i++) {
    const delta = phase[i] + offset - unwrapped[i - 1];
    if (delta > Math.PI) offset -= 2 * Math.PI;
    if (delta < -Math.PI) offset += 2 * Math.PI;
    unwrapped[i] = phase[i] + offset;
  }
  return unwrapped;
}

function wrapPhase(value) {
  while (value > Math.PI) value -= 2 * Math.PI;
  while (value < -Math.PI) value += 2 * Math.PI;
  return value;
}

function spectrumOf(signal, fftSize) {
  const plan = getFFTPlan(fftSize);
  const real = new Float64Array(fftSize);
  const imag = new Float64Array(fftSize);
  real.set(signal.subarray(0, Math.min(signal.length, fftSize)));
  plan.transform(real, imag, false);
  return { real, imag };
}

export function analyseHighFrequencyPhase(reference, modeOutputs, sampleRate, options = {}) {
  const fftSize = nextPowerOfTwo(Math.max(reference.length, ...modeOutputs.map((item) => item.signal.length)));
  const expected = spectrumOf(reference, fftSize);
  const ratios = options.ratios ?? [0.9, 0.95, 0.98, 0.99, 0.995];
  const rows = [];
  const phases = new Float64Array(expected.real.length / 2 + 1);
  for (let i = 0; i < phases.length; i++) phases[i] = Math.atan2(expected.imag[i], expected.real[i]);
  const unwrapped = unwrapPhase(phases);
  const peak = reference.reduce((max, value) => Math.max(max, Math.abs(value)), 0);

  for (const item of modeOutputs) {
    item.actual = spectrumOf(item.signal, fftSize);
  }

  for (const ratio of ratios) {
    const bin = Math.min(
      phases.length - 1,
      Math.max(1, Math.round((ratio * sampleRate * 0.5 * fftSize) / sampleRate)),
    );
    const expectedMagnitude = Math.hypot(expected.real[bin], expected.imag[bin]);
    const row = {
      ratioOfNyquist: ratio,
      frequencyHz: (bin * sampleRate) / fftSize,
      magnitudeDb: 20 * Math.log10(expectedMagnitude / Math.max(peak, 1e-15)),
      expectedPhaseRad: phases[bin],
      unwrappedPhaseRad: unwrapped[bin],
      modes: {},
    };
    for (const item of modeOutputs) {
      const actualPhase = Math.atan2(item.actual.imag[bin], item.actual.real[bin]);
      const phaseDelta = wrapPhase(actualPhase - phases[bin]);
      row.modes[item.name] = {
        phaseRad: actualPhase,
        unwrappedPhaseRad: unwrapped[bin] + phaseDelta,
        phaseDeltaRad: phaseDelta,
      };
    }
    rows.push(row);
  }

  const summary = {};
  for (const item of modeOutputs) {
    let maxAbsDelta = 0;
    for (const row of rows) {
      maxAbsDelta = Math.max(maxAbsDelta, Math.abs(row.modes[item.name].phaseDeltaRad));
    }
    summary[item.name] = {
      maxAbsPhaseDeltaRad: maxAbsDelta,
      maxAbsPhaseDeltaDeg: (maxAbsDelta * 180) / Math.PI,
      noPhaseInversion: maxAbsDelta < 0.02,
    };
  }

  return {
    fftSize,
    binHz: sampleRate / fftSize,
    rows,
    summary,
  };
}


