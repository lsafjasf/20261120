import test from 'node:test';
import assert from 'node:assert/strict';
import { getFFTPlan } from '../src/fft.js';
import { createConvolver } from '../src/partitioned-convolution.js';
import {
  analyseHighFrequencyPhase,
  differenceStatistics,
  fftConvolve,
  generateDecayingImpulseResponse,
  generateLinearPhaseImpulseResponse,
  generateWhiteNoise,
  runConvolver,
} from '../src/measurement.js';

function directConvolve(signal, ir) {
  const output = new Float64Array(signal.length);
  for (let n = 0; n < output.length; n++) {
    let sum = 0;
    const start = Math.max(0, n - ir.length + 1);
    for (let j = start; j <= n; j++) {
      sum += signal[j] * ir[n - j];
    }
    output[n] = sum;
  }
  return output;
}

test('FFT round trip preserves samples', () => {
  const size = 256;
  const plan = getFFTPlan(size);
  const real = new Float64Array(size);
  const imag = new Float64Array(size);
  for (let i = 0; i < size; i++) real[i] = Math.sin(i * 0.17) + Math.cos(i * 0.31);
  const original = Float64Array.from(real);
  plan.transform(real, imag, false);
  plan.transform(real, imag, true);
  for (let i = 0; i < size; i++) {
    assert.ok(Math.abs(real[i] - original[i]) < 1e-11);
    assert.ok(Math.abs(imag[i]) < 1e-11);
  }
});

test('uniform and non-uniform match direct convolution', () => {
  const blockSize = 128;
  const ir = generateDecayingImpulseResponse(blockSize * 7, 48000, 7);
  const input = generateWhiteNoise(blockSize * 20, 11);
  const expected = directConvolve(input, ir);
  for (const mode of ['uniform', 'non-uniform']) {
    const convolver = createConvolver(mode, ir, { blockSize, sampleRate: 48000 });
    const { output } = runConvolver(convolver, input);
    let maxError = 0;
    for (let i = 0; i < input.length; i++) {
      maxError = Math.max(maxError, Math.abs(output[i] - expected[i]));
    }
    assert.ok(maxError < 2e-6, `${mode} max error ${maxError}`);
  }
});

test('partition boundaries do not create a periodic residual', () => {
  const blockSize = 128;
  const sampleRate = 48000;
  const ir = generateDecayingImpulseResponse(blockSize * 160, sampleRate, 19);
  const input = generateWhiteNoise(blockSize * 420, 23);
  for (let i = 0; i < input.length; i += blockSize) input[i] += 0.8;
  const reference = fftConvolve(input, ir);
  const outputs = {};

  for (const mode of ['uniform', 'non-uniform']) {
    const convolver = createConvolver(mode, ir, { blockSize, sampleRate });
    outputs[mode] = runConvolver(convolver, input).output;
    const comparison = differenceStatistics(reference, outputs[mode], blockSize);
    assert.ok(comparison.maxAbs < 2e-5, `${mode} reference max error`);
    assert.ok(Math.abs(comparison.boundary.boundaryToInteriorDb) < 6);
    assert.ok(comparison.boundary.blockRateCombDb < 6);
  }

  const modeDifference = differenceStatistics(outputs.uniform, outputs['non-uniform'], blockSize);
  assert.ok(modeDifference.maxAbs < 2e-5);
  assert.ok(Math.abs(modeDifference.boundary.boundaryToInteriorDb) < 6);
});

test('high-frequency phase follows the target impulse response', () => {
  const blockSize = 128;
  const sampleRate = 48000;
  const ir = generateLinearPhaseImpulseResponse(blockSize * 96, sampleRate, 0.998);
  const impulse = new Float64Array(blockSize * 100);
  impulse[0] = 1;
  const modeOutputs = [];
  for (const mode of ['uniform', 'non-uniform']) {
    const convolver = createConvolver(mode, ir, { blockSize, sampleRate });
    modeOutputs.push({
      name: mode,
      signal: runConvolver(convolver, impulse).output,
    });
  }
  const phase = analyseHighFrequencyPhase(ir, modeOutputs, sampleRate);
  assert.ok(phase.summary.uniform.noPhaseInversion);
  assert.ok(phase.summary['non-uniform'].noPhaseInversion);
});

