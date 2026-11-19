import { mkdirSync, writeFileSync } from 'node:fs';
import { cpus, platform, release } from 'node:os';
import { dirname, resolve } from 'node:path';
import { createConvolver, DEFAULT_BLOCK_SIZE } from '../src/partitioned-convolution.js';
import {
  analyseHighFrequencyPhase,
  createRandom,
  differenceStatistics,
  fftConvolve,
  generateDecayingImpulseResponse,
  generateLinearPhaseImpulseResponse,
  runConvolver,
  timeStatistics,
} from '../src/measurement.js';

const SAMPLE_RATE = 48000;
const BLOCK_SIZE = DEFAULT_BLOCK_SIZE;
const IR_SECONDS = 2;
const PROBE_SECONDS = 4;
const root = resolve(import.meta.dirname, '..');
const consoleCpu = cpus()[0]?.model || 'unknown CPU';

function makeProbe(length) {
  const random = createRandom(0xc0ffee);
  const signal = new Float64Array(length);
  for (let i = 0; i < length; i++) signal[i] = random() * 2 - 1;
  for (let i = 0; i < length; i += BLOCK_SIZE) {
    signal[i] += (i / BLOCK_SIZE) % 2 === 0 ? 1.2 : -1.2;
  }
  return signal;
}

function runMode(mode, ir, input, rounds = 3) {
  const times = [];
  const setupTimes = [];
  let output = null;
  let metrics = null;

  for (let round = 0; round < rounds; round++) {
    const setupStart = performance.now();
    const convolver = createConvolver(mode, ir, {
      blockSize: BLOCK_SIZE,
      sampleRate: SAMPLE_RATE,
    });
    setupTimes.push(performance.now() - setupStart);
    const result = runConvolver(convolver, input, {
      measure: true,
      warmupBlocks: 64,
    });
    if (!output) output = result.output;
    times.push(...result.blockTimesUs);
    metrics = convolver.getMetrics();
  }

  return {
    output,
    times,
    setupMs: setupTimes.reduce((sum, value) => sum + value, 0) / setupTimes.length,
    metrics,
    rounds,
  };
}

function phaseSignals(ir, outputLength) {
  const impulse = new Float64Array(outputLength);
  impulse[0] = 1;
  return ['uniform', 'non-uniform'].map((mode) => {
    const convolver = createConvolver(mode, ir, {
      blockSize: BLOCK_SIZE,
      sampleRate: SAMPLE_RATE,
    });
    return {
      name: mode,
      signal: runConvolver(convolver, impulse).output,
    };
  });
}

function makeMarkdown(report) {
  const { cpu, referenceError, sameIrOutputDifference, phase } = report;
  const latencyMs = ((BLOCK_SIZE / SAMPLE_RATE) * 1000).toFixed(3);
  const rows = phase.rows.map((row) => (
    `| ${(row.ratioOfNyquist * 100).toFixed(1)}% | ${row.frequencyHz.toFixed(1)} | `
    + `${row.magnitudeDb.toFixed(2)} | ${row.unwrappedPhaseRad.toFixed(2)} | `
    + `${(row.modes.uniform.phaseDeltaRad * 180 / Math.PI).toExponential(2)} | `
    + `${(row.modes['non-uniform'].phaseDeltaRad * 180 / Math.PI).toExponential(2)} |`
  ));
  return `# 分区卷积测量报告\n\n`
    + `生成时间：${report.generatedAt}\n\n`
    + `配置：${SAMPLE_RATE} Hz，块大小 ${BLOCK_SIZE}，IR ${report.config.impulseResponseSamples} taps `
    + `(${IR_SECONDS} s)，探针 ${PROBE_SECONDS} s。\n\n`
    + `## CPU 与延迟\n\n`
    + `| 模式 | 平均 CPU | p95 CPU | p99 CPU | 平均块耗时 | 实时倍率 | 处理延迟 |\n`
    + `| --- | ---: | ---: | ---: | ---: | ---: | ---: |\n`
    + `| UPOLS | ${cpu.uniform.cpuPercentMean.toFixed(2)}% | ${cpu.uniform.cpuPercentP95.toFixed(2)}% | ${cpu.uniform.cpuPercentP99.toFixed(2)}% | ${cpu.uniform.meanUs.toFixed(1)} µs | ${cpu.uniform.realtimeFactor.toFixed(2)}× | ${latencyMs} ms |\n`
    + `| NUPOLS | ${cpu.nonUniform.cpuPercentMean.toFixed(2)}% | ${cpu.nonUniform.cpuPercentP95.toFixed(2)}% | ${cpu.nonUniform.cpuPercentP99.toFixed(2)}% | ${cpu.nonUniform.meanUs.toFixed(1)} µs | ${cpu.nonUniform.realtimeFactor.toFixed(2)}× | ${latencyMs} ms |\n\n`
    + `## 同一 IR 的输出差异\n\n`
    + `最大绝对差：\`${sameIrOutputDifference.maxAbs.toExponential(3)}\`；`
    + `RMS：\`${sameIrOutputDifference.rms.toExponential(3)}\`；`
    + `SNR：\`${sameIrOutputDifference.snrDb.toFixed(1)} dB\`。\n\n`
    + `## 分区边界\n\n`
    + `| 模式 | 对参考最大误差 | 边界/内部残差 | 块率周期分量 |\n`
    + `| --- | ---: | ---: | ---: |\n`
    + `| UPOLS | ${referenceError.uniform.maxAbs.toExponential(3)} | ${referenceError.uniform.boundary.boundaryToInteriorDb.toFixed(2)} dB | ${referenceError.uniform.boundary.blockRateCombDb.toFixed(2)} dB |\n`
    + `| NUPOLS | ${referenceError.nonUniform.maxAbs.toExponential(3)} | ${referenceError.nonUniform.boundary.boundaryToInteriorDb.toFixed(2)} dB | ${referenceError.nonUniform.boundary.blockRateCombDb.toFixed(2)} dB |\n\n`
    + `接缝检查：${report.checks.noBoundaryEmphasis && report.checks.noBlockRateComb ? '通过' : '未通过'}。\n\n`
    + `## 奈奎斯特附近相位\n\n`
    + `| Nyquist | Hz | 幅度 dB | 展开相位 rad | UPOLS Δφ° | NUPOLS Δφ° |\n`
    + `| ---: | ---: | ---: | ---: | ---: | ---: |\n${rows.join('\n')}\n\n`
    + `两模式最大相位误差：UPOLS ${phase.summary.uniform.maxAbsPhaseDeltaDeg.toExponential(2)}°，`
    + `NUPOLS ${phase.summary['non-uniform'].maxAbsPhaseDeltaDeg.toExponential(2)}°。`
    + `高频相位反转检查：${report.checks.highFrequencyNoPhaseInversion ? '通过' : '未通过'}。\n`;
}

const ir = generateDecayingImpulseResponse(
  Math.round(SAMPLE_RATE * IR_SECONDS),
  SAMPLE_RATE,
);
const input = makeProbe(Math.round(SAMPLE_RATE * PROBE_SECONDS));
const blockBudgetUs = (BLOCK_SIZE / SAMPLE_RATE) * 1e6;

console.log('Running uniform partition convolution...');
const uniform = runMode('uniform', ir, input);
console.log('Running non-uniform partition convolution...');
const nonUniform = runMode('non-uniform', ir, input);

console.log('Computing FFT reference and boundary metrics...');
const reference = fftConvolve(input, ir).subarray(0, input.length);
const uniformError = differenceStatistics(reference, uniform.output, BLOCK_SIZE);
const nonUniformError = differenceStatistics(reference, nonUniform.output, BLOCK_SIZE);
const modeDifference = differenceStatistics(uniform.output, nonUniform.output, BLOCK_SIZE);

console.log('Measuring high-frequency phase...');
const phaseIr = generateLinearPhaseImpulseResponse(ir.length, SAMPLE_RATE, 0.998);
const phase = analyseHighFrequencyPhase(
  phaseIr,
  phaseSignals(phaseIr, ir.length + BLOCK_SIZE),
  SAMPLE_RATE,
);

const report = {
  generatedAt: new Date().toISOString(),
  environment: {
    runtime: `Node.js ${process.version}`,
    platform: `${platform()} ${release()}`,
    cpu: consoleCpu,
  },
  config: {
    sampleRate: SAMPLE_RATE,
    blockSize: BLOCK_SIZE,
    blockBudgetUs,
    impulseResponseSamples: ir.length,
    impulseResponseSeconds: IR_SECONDS,
    probeSeconds: PROBE_SECONDS,
    probeBlocks: Math.ceil(input.length / BLOCK_SIZE),
  },
  latency: {
    uniform: {
      samples: BLOCK_SIZE,
      milliseconds: (BLOCK_SIZE / SAMPLE_RATE) * 1000,
    },
    nonUniform: {
      samples: BLOCK_SIZE,
      milliseconds: (BLOCK_SIZE / SAMPLE_RATE) * 1000,
    },
  },
  cpu: {
    uniform: {
      ...timeStatistics(uniform.times, SAMPLE_RATE, BLOCK_SIZE),
      setupMs: uniform.setupMs,
      rounds: uniform.rounds,
      architecture: uniform.metrics,
    },
    nonUniform: {
      ...timeStatistics(nonUniform.times, SAMPLE_RATE, BLOCK_SIZE),
      setupMs: nonUniform.setupMs,
      rounds: nonUniform.rounds,
      architecture: nonUniform.metrics,
    },
  },
  referenceError: {
    uniform: uniformError,
    nonUniform: nonUniformError,
  },
  sameIrOutputDifference: modeDifference,
  phase,
  checks: {
    noBoundaryEmphasis: Math.abs(uniformError.boundary.boundaryToInteriorDb) < 6
      && Math.abs(nonUniformError.boundary.boundaryToInteriorDb) < 6,
    noBlockRateComb: uniformError.boundary.blockRateCombDb < 6
      && nonUniformError.boundary.blockRateCombDb < 6,
    highFrequencyNoPhaseInversion: phase.summary.uniform.noPhaseInversion
      && phase.summary['non-uniform'].noPhaseInversion,
    outputsMatch: modeDifference.maxAbs < 2e-5,
  },
};

const jsonPath = resolve(root, 'artifacts/results/comparison.json');
const markdownPath = resolve(root, 'reports/partition-comparison.md');
mkdirSync(dirname(jsonPath), { recursive: true });
mkdirSync(dirname(markdownPath), { recursive: true });
writeFileSync(jsonPath, `${JSON.stringify(report, null, 2)}\n`);
writeFileSync(markdownPath, makeMarkdown(report));

console.log(`\nCPU: UPOLS ${report.cpu.uniform.cpuPercentMean.toFixed(2)}% mean, `
  + `NUPOLS ${report.cpu.nonUniform.cpuPercentMean.toFixed(2)}% mean`);
console.log(`Latency: ${report.latency.uniform.milliseconds.toFixed(3)} ms both`);
console.log(`Output max difference: ${modeDifference.maxAbs.toExponential(3)}`);
console.log(`Boundary check: ${report.checks.noBoundaryEmphasis ? 'pass' : 'fail'}`);
console.log(`Phase check: ${report.checks.highFrequencyNoPhaseInversion ? 'pass' : 'fail'}`);
console.log(`Wrote ${jsonPath}`);
console.log(`Wrote ${markdownPath}`);

