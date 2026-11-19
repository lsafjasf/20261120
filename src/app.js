import { createConvolver, DEFAULT_BLOCK_SIZE } from './partitioned-convolution.js';
import {
  analyseHighFrequencyPhase,
  createRandom,
  differenceStatistics,
  fftConvolve,
  generateDecayingImpulseResponse,
  generateLinearPhaseImpulseResponse,
  generateWhiteNoise,
  runConvolver,
  timeStatistics,
} from './measurement.js';

const $ = (selector) => document.querySelector(selector);
const sampleRate = 48000;
const blockSize = DEFAULT_BLOCK_SIZE;
let mode = 'uniform';
let impulseResponse = null;
let audioContext = null;
let workletNode = null;
let sourceNode = null;
let gainNode = null;
let started = false;
let analysisRunning = false;

const ui = {
  status: $('#status'),
  audioButton: $('#audioButton'),
  analysisButton: $('#analysisButton'),
  analysisStatus: $('#analysisStatus'),
  irDuration: $('#irDuration'),
  sourceType: $('#sourceType'),
  volume: $('#volume'),
  irFile: $('#irFile'),
  workletCpu: $('#workletCpu'),
  workletP99: $('#workletP99'),
  latencyMetric: $('#latencyMetric'),
  partitionMetric: $('#partitionMetric'),
  fftMetric: $('#fftMetric'),
  footprintMetric: $('#footprintMetric'),
  diffMax: $('#diffMax'),
  boundaryRatio: $('#boundaryRatio'),
  combMetric: $('#combMetric'),
  phaseDelta: $('#phaseDelta'),
  phaseBadge: $('#phaseBadge'),
  phaseTable: $('#phaseTable'),
  reportPre: $('#reportPre'),
  diffCanvas: $('#diffCanvas'),
  cpuCanvas: $('#cpuCanvas'),
  phaseCanvas: $('#phaseCanvas'),
};

function setStatus(text, error = false) {
  ui.status.textContent = text;
  ui.status.classList.toggle('error', error);
}

function formatPercent(value) {
  return Number.isFinite(value) ? `${value.toFixed(2)}%` : '—';
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes)) return '—';
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

function makeImpulseResponse(durationSeconds) {
  return generateDecayingImpulseResponse(
    Math.round(sampleRate * durationSeconds),
    sampleRate,
    0x4f3a,
  );
}

function updateModeMetrics() {
  const convolver = createConvolver(mode, impulseResponse, { blockSize, sampleRate });
  const metrics = convolver.getMetrics(1);
  ui.partitionMetric.textContent = mode === 'uniform'
    ? `${metrics.partitionCount} 个 128-tap 分区`
    : `${metrics.levelCount} 个尺度 / ${metrics.partitionCount} 段`;
  ui.fftMetric.textContent = mode === 'uniform'
    ? '2 FFT / block · 750 个复乘向量'
    : `≈ ${metrics.fftCallsPerBlock.toFixed(2)} FFT / block`;
  ui.footprintMetric.textContent = formatBytes(metrics.footprintBytes);
  ui.latencyMetric.textContent = `${((blockSize / sampleRate) * 1000).toFixed(3)} ms`;
  return metrics;
}

function makeTestBuffer(type) {
  const seconds = type === 'sweep' ? 7 : 4;
  const length = Math.round(audioContext.sampleRate * seconds);
  const buffer = audioContext.createBuffer(1, length, audioContext.sampleRate);
  const channel = buffer.getChannelData(0);
  const random = createRandom(type === 'noise' ? 0x71c0 : 0x29e1);

  if (type === 'noise') {
    for (let i = 0; i < length; i++) {
      channel[i] = (random() * 2 - 1) * 0.16;
      if (i % blockSize === 0 && i % (blockSize * 16) === 0) {
        channel[i] += (i / blockSize) % 2 === 0 ? 0.45 : -0.45;
      }
    }
  } else if (type === 'sweep') {
    const duration = length / audioContext.sampleRate;
    const lower = 20;
    const upper = Math.min(20000, audioContext.sampleRate * 0.49);
    const ratio = upper / lower;
    for (let i = 0; i < length; i++) {
      const t = i / audioContext.sampleRate;
      const phase = 2 * Math.PI * lower * duration / Math.log(ratio)
        * (Math.pow(ratio, t / duration) - 1);
      channel[i] = Math.sin(phase) * 0.18;
    }
  } else {
    for (let i = 0; i < length; i++) {
      channel[i] = Math.sin((2 * Math.PI * 16000 * i) / audioContext.sampleRate) * 0.18;
    }
  }
  return buffer;
}

function rebuildSource() {
  if (!started) return;
  if (sourceNode) {
    sourceNode.stop();
    sourceNode.disconnect();
  }
  sourceNode = audioContext.createBufferSource();
  sourceNode.buffer = makeTestBuffer(ui.sourceType.value);
  sourceNode.loop = true;
  sourceNode.connect(workletNode);
  sourceNode.start();
}

async function ensureAudioGraph() {
  if (audioContext) return;
  audioContext = new AudioContext({ latencyHint: 'interactive', sampleRate });
  await audioContext.audioWorklet.addModule('./src/worklet-processor.js');
  workletNode = new AudioWorkletNode(audioContext, 'low-latency-convolution', {
    numberOfInputs: 1,
    numberOfOutputs: 1,
    outputChannelCount: [1],
    processorOptions: {
      mode,
      impulseResponse: Float32Array.from(impulseResponse),
    },
  });
  gainNode = audioContext.createGain();
  gainNode.gain.value = Number(ui.volume.value);
  workletNode.connect(gainNode).connect(audioContext.destination);
  workletNode.port.onmessage = handleWorkletMessage;
  setStatus('AudioWorklet 已加载。');
}

async function toggleAudio() {
  try {
    if (!started) {
      await ensureAudioGraph();
      await audioContext.resume();
      rebuildSource();
      started = true;
      ui.audioButton.textContent = '停止实时处理';
      setStatus(`实时处理中：${mode === 'uniform' ? '均匀分区' : '非均匀分区'}。`);
    } else {
      sourceNode?.stop();
      await audioContext.suspend();
      started = false;
      ui.audioButton.textContent = '启动实时 AudioWorklet';
      setStatus('实时处理已暂停。');
    }
  } catch (error) {
    console.error(error);
    setStatus(`音频启动失败：${error.message}`, true);
  }
}

function handleWorkletMessage(event) {
  const message = event.data;
  if (message.type === 'cpu') {
    ui.workletCpu.textContent = formatPercent(message.cpuPercent);
    ui.workletP99.textContent = `p99 ${formatPercent(message.p99CpuPercent)} · max ${formatPercent((message.maxUs / 2666.6667) * 100)}`;
  } else if (message.type === 'configured') {
    setStatus(`AudioWorklet 已配置 ${message.impulseResponseSamples.toLocaleString()} taps。`);
  } else if (message.type === 'mode') {
    setStatus(`实时模式切换为${message.mode === 'uniform' ? '均匀分区' : '非均匀分区'}。`);
  }
}

function selectMode(nextMode) {
  mode = nextMode;
  document.querySelectorAll('.mode').forEach((button) => {
    button.classList.toggle('active', button.dataset.mode === mode);
  });
  updateModeMetrics();
  if (workletNode) workletNode.port.postMessage({ type: 'set-mode', mode });
}

function loadImpulseResponse(samples) {
  impulseResponse = Float64Array.from(samples);
  updateModeMetrics();
  if (workletNode) {
    workletNode.port.postMessage({
      type: 'set-ir',
      impulseResponse: Float32Array.from(impulseResponse),
    });
  }
}

async function loadImpulseResponseFile(file) {
  try {
    if (!audioContext) {
      audioContext = new AudioContext({ sampleRate });
    }
    const bytes = await file.arrayBuffer();
    const decoded = await audioContext.decodeAudioData(bytes);
    const mono = new Float32Array(decoded.length);
    for (let channel = 0; channel < decoded.numberOfChannels; channel++) {
      const data = decoded.getChannelData(channel);
      for (let i = 0; i < data.length; i++) mono[i] += data[i] / decoded.numberOfChannels;
    }
    loadImpulseResponse(mono);
    setStatus(`已载入 ${file.name}，${decoded.length.toLocaleString()} taps。`);
  } catch (error) {
    console.error(error);
    setStatus(`IR 载入失败：${error.message}`, true);
  }
}

async function initialize() {
  impulseResponse = makeImpulseResponse(Number(ui.irDuration.value));
  updateModeMetrics();
  ui.audioButton.addEventListener('click', toggleAudio);
  ui.analysisButton.addEventListener('click', runOfflineComparison);
  document.querySelectorAll('.mode').forEach((button) => {
    button.addEventListener('click', () => selectMode(button.dataset.mode));
  });
  ui.irDuration.addEventListener('change', () => {
    loadImpulseResponse(makeImpulseResponse(Number(ui.irDuration.value)));
    setStatus(`已生成 ${ui.irDuration.value} 秒长脉冲响应。`);
  });
  ui.sourceType.addEventListener('change', rebuildSource);
  ui.volume.addEventListener('input', () => {
    if (gainNode) gainNode.gain.value = Number(ui.volume.value);
  });
  ui.irFile.addEventListener('change', () => {
    const file = ui.irFile.files?.[0];
    if (file) loadImpulseResponseFile(file);
  });
}

initialize();

function makeProbeSignal(length) {
  const random = createRandom(0x9876);
  const signal = new Float64Array(length);
  for (let i = 0; i < length; i++) signal[i] = random() * 2 - 1;
  for (let i = 0; i < length; i += blockSize) {
    signal[i] += (i / blockSize) % 2 === 0 ? 1.2 : -1.2;
  }
  return signal;
}

function measureMode(modeName, input) {
  const convolver = createConvolver(modeName, impulseResponse, { blockSize, sampleRate });
  const warmup = Math.min(64, Math.floor(input.length / blockSize));
  const result = runConvolver(convolver, input, {
    measure: true,
    warmupBlocks: warmup,
  });
  return {
    convolver,
    output: result.output,
    times: result.blockTimesUs,
    metrics: convolver.getMetrics(),
  };
}

function phaseSignals(phaseImpulseResponse, length) {
  const impulse = new Float64Array(length + blockSize);
  impulse[0] = 1;
  return ['uniform', 'non-uniform'].map((modeName) => {
    const convolver = createConvolver(modeName, phaseImpulseResponse, { blockSize, sampleRate });
    return {
      name: modeName,
      signal: runConvolver(convolver, impulse).output,
    };
  });
}

async function runOfflineComparison() {
  if (analysisRunning) return;
  analysisRunning = true;
  ui.analysisButton.disabled = true;
  ui.analysisStatus.textContent = '测量中…';
  ui.reportPre.textContent = '正在运行 FFT 参考卷积和两种分区实现…';
  await new Promise((resolve) => setTimeout(resolve, 30));

  try {
    const inputLength = impulseResponse.length;
    const input = makeProbeSignal(inputLength);
    const reference = fftConvolve(input, impulseResponse).subarray(0, inputLength);
    const uniform = measureMode('uniform', input);
    const nonUniform = measureMode('non-uniform', input);
    const uniformStats = timeStatistics(uniform.times, sampleRate, blockSize);
    const nonUniformStats = timeStatistics(nonUniform.times, sampleRate, blockSize);
    const uniformError = differenceStatistics(reference, uniform.output, blockSize);
    const nonUniformError = differenceStatistics(reference, nonUniform.output, blockSize);
    const modeDifference = differenceStatistics(uniform.output, nonUniform.output, blockSize);

    const phaseIr = generateLinearPhaseImpulseResponse(
      impulseResponse.length,
      sampleRate,
      0.998,
    );
    const phase = analyseHighFrequencyPhase(
      phaseIr,
      phaseSignals(phaseIr, impulseResponse.length),
      sampleRate,
    );

    const report = {
      generatedAt: new Date().toISOString(),
      config: {
        sampleRate,
        blockSize,
        blockBudgetMs: (blockSize / sampleRate) * 1000,
        impulseResponseSamples: impulseResponse.length,
        impulseResponseSeconds: impulseResponse.length / sampleRate,
        probeSeconds: input.length / sampleRate,
      },
      latency: {
        uniform: `${((blockSize / sampleRate) * 1000).toFixed(3)} ms`,
        nonUniform: `${((blockSize / sampleRate) * 1000).toFixed(3)} ms`,
      },
      cpu: {
        uniform: { ...uniformStats, architecture: uniform.metrics },
        nonUniform: { ...nonUniformStats, architecture: nonUniform.metrics },
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
      },
    };

    renderComparison(report, uniform.output, nonUniform.output);
    ui.analysisStatus.textContent = Object.values(report.checks).every(Boolean)
      ? '全部检查通过'
      : '存在需检查项';
    ui.reportPre.textContent = JSON.stringify(report, null, 2);
  } catch (error) {
    console.error(error);
    ui.analysisStatus.textContent = '测量失败';
    ui.reportPre.textContent = error.stack || error.message;
  } finally {
    analysisRunning = false;
    ui.analysisButton.disabled = false;
  }
}

function prepareCanvas(canvas, height) {
  const width = Math.max(280, canvas.clientWidth || 560);
  const ratio = Math.min(2, window.devicePixelRatio || 1);
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  canvas.style.height = `${height}px`;
  const context = canvas.getContext('2d');
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, width, height);
  context.lineWidth = 1;
  return { context, width, height };
}

function drawGrid(context, width, height, rows = 4) {
  context.strokeStyle = 'rgba(139, 164, 177, 0.16)';
  for (let row = 0; row <= rows; row++) {
    const y = (row / rows) * height;
    context.beginPath();
    context.moveTo(0, y);
    context.lineTo(width, y);
    context.stroke();
  }
  for (let column = 0; column <= 6; column++) {
    const x = (column / 6) * width;
    context.beginPath();
    context.moveTo(x, 0);
    context.lineTo(x, height);
    context.stroke();
  }
}

function drawDifferenceChart(uniformOutput, nonUniformOutput) {
  const { context, width, height } = prepareCanvas(ui.diffCanvas, 230);
  const start = blockSize * 32;
  const span = blockSize * 4;
  const values = new Float32Array(span);
  let peak = 1e-12;
  for (let i = 0; i < span; i++) {
    const value = uniformOutput[start + i] - nonUniformOutput[start + i];
    values[i] = value;
    peak = Math.max(peak, Math.abs(value));
  }

  drawGrid(context, width, height);
  const boundaryX = width * (blockSize / span);
  context.strokeStyle = 'rgba(255, 186, 104, 0.7)';
  context.setLineDash([4, 4]);
  context.beginPath();
  context.moveTo(boundaryX, 0);
  context.lineTo(boundaryX, height);
  context.stroke();
  context.setLineDash([]);

  context.strokeStyle = '#56e0d0';
  context.lineWidth = 1.5;
  context.beginPath();
  for (let i = 0; i < span; i++) {
    const x = (i / (span - 1)) * width;
    const y = height / 2 - (values[i] / peak) * (height * 0.36);
    if (i === 0) context.moveTo(x, y);
    else context.lineTo(x, y);
  }
  context.stroke();
  context.fillStyle = '#8ba4b1';
  context.font = '11px Inter, sans-serif';
  context.fillText(`±${peak.toExponential(2)}（自动增益）`, 10, 17);
  context.fillText('128-sample boundary', boundaryX + 8, height - 12);
}

function drawCpuChart(uniformStats, nonUniformStats) {
  const { context, width, height } = prepareCanvas(ui.cpuCanvas, 230);
  drawGrid(context, width, height, 4);
  const maximum = Math.max(
    1,
    uniformStats.cpuPercentP99,
    nonUniformStats.cpuPercentP99,
  ) * 1.15;
  const groups = [
    { label: 'UPOLS', color: '#56e0d0', stats: uniformStats },
    { label: 'NUPOLS', color: '#c9f06b', stats: nonUniformStats },
  ];
  const left = 42;
  const bottom = height - 32;
  const usableHeight = height - 48;
  const groupWidth = (width - left - 16) / groups.length;

  context.fillStyle = '#8ba4b1';
  context.font = '11px Inter, sans-serif';
  for (let tick = 0; tick <= 4; tick++) {
    const value = (maximum * tick) / 4;
    const y = bottom - (tick / 4) * usableHeight;
    context.fillText(`${value.toFixed(0)}%`, 3, y + 4);
  }

  groups.forEach((group, groupIndex) => {
    const center = left + groupWidth * (groupIndex + 0.5);
    const bars = [
      { label: 'mean', value: group.stats.cpuPercentMean },
      { label: 'p99', value: group.stats.cpuPercentP99 },
    ];
    bars.forEach((bar, barIndex) => {
      const barWidth = Math.min(56, groupWidth * 0.27);
      const x = center + (barIndex - 0.5) * barWidth * 1.5 - barWidth / 2;
      const barHeight = (bar.value / maximum) * usableHeight;
      context.fillStyle = barIndex === 0 ? group.color : `${group.color}66`;
      context.fillRect(x, bottom - barHeight, barWidth, barHeight);
      context.fillStyle = '#edf7fa';
      context.textAlign = 'center';
      context.fillText(`${bar.value.toFixed(1)}%`, x + barWidth / 2, bottom - barHeight - 6);
      context.fillStyle = '#8ba4b1';
      context.fillText(bar.label, x + barWidth / 2, bottom + 15);
    });
    context.fillStyle = group.color;
    context.font = '600 12px Inter, sans-serif';
    context.textAlign = 'center';
    context.fillText(group.label, center, height - 5);
    context.font = '11px Inter, sans-serif';
  });
  context.textAlign = 'left';
}

function drawPhaseChart(phase) {
  const { context, width, height } = prepareCanvas(ui.phaseCanvas, 260);
  const topHeight = height * 0.56;
  const bottomTop = height * 0.7;
  const bottomHeight = height - bottomTop - 18;
  drawGrid(context, width, topHeight, 3);
  context.strokeStyle = 'rgba(139, 164, 177, 0.25)';
  context.beginPath();
  context.moveTo(0, bottomTop);
  context.lineTo(width, bottomTop);
  context.stroke();

  const phases = phase.rows.map((row) => row.unwrappedPhaseRad / Math.PI);
  const low = Math.min(...phases);
  const high = Math.max(...phases);
  const pad = Math.max(0.2, (high - low) * 0.08);
  const xFor = (index) => 14 + (index / Math.max(1, phase.rows.length - 1)) * (width - 28);
  const yFor = (value) => 10 + (1 - (value - low + pad) / (high - low + 2 * pad)) * (topHeight - 20);

  context.strokeStyle = '#8ba4b1';
  context.lineWidth = 1.5;
  context.beginPath();
  phase.rows.forEach((row, index) => {
    const x = xFor(index);
    const y = yFor(row.unwrappedPhaseRad / Math.PI);
    if (index === 0) context.moveTo(x, y);
    else context.lineTo(x, y);
  });
  context.stroke();

  const colors = { uniform: '#56e0d0', 'non-uniform': '#c9f06b' };
  for (const name of ['uniform', 'non-uniform']) {
    context.fillStyle = colors[name];
    context.strokeStyle = colors[name];
    context.lineWidth = 1.5;
    context.beginPath();
    phase.rows.forEach((row, index) => {
      const measured = row.modes[name].unwrappedPhaseRad / Math.PI;
      const x = xFor(index);
      const y = yFor(measured);
      if (index === 0) context.moveTo(x, y);
      else context.lineTo(x, y);
    });
    context.stroke();
    phase.rows.forEach((row, index) => {
      const measured = row.modes[name].unwrappedPhaseRad / Math.PI;
      context.beginPath();
      context.arc(xFor(index), yFor(measured), 3.5, 0, Math.PI * 2);
      context.fill();
    });
  }
  context.fillStyle = '#8ba4b1';
  context.font = '11px Inter, sans-serif';
  context.fillText('展开相位 / π（灰线为参考，彩色线为两模式实测）', 10, 14);

  context.fillStyle = '#8ba4b1';
  context.fillText('相位误差 Δφ / °（0° 参考线）', 10, bottomTop - 6);
  const zeroY = bottomTop + bottomHeight / 2;
  context.strokeStyle = 'rgba(255, 186, 104, 0.6)';
  context.beginPath();
  context.moveTo(0, zeroY);
  context.lineTo(width, zeroY);
  context.stroke();

  for (const name of ['uniform', 'non-uniform']) {
    context.strokeStyle = colors[name];
    context.lineWidth = 2;
    context.beginPath();
    phase.rows.forEach((row, index) => {
      const degrees = (row.modes[name].phaseDeltaRad * 180) / Math.PI;
      const y = zeroY - (degrees / 5) * (bottomHeight / 2);
      if (index === 0) context.moveTo(xFor(index), y);
      else context.lineTo(xFor(index), y);
    });
    context.stroke();
  }
}

function renderComparison(report, uniformOutput, nonUniformOutput) {
  ui.diffMax.textContent = report.sameIrOutputDifference.maxAbs.toExponential(2);
  ui.boundaryRatio.textContent = `${report.referenceError.uniform.boundary.boundaryToInteriorDb.toFixed(2)} / ${report.referenceError.nonUniform.boundary.boundaryToInteriorDb.toFixed(2)} dB`;
  ui.combMetric.textContent = `${report.referenceError.uniform.boundary.blockRateCombDb.toFixed(2)} / ${report.referenceError.nonUniform.boundary.blockRateCombDb.toFixed(2)} dB`;
  const maxPhaseDeg = Math.max(
    report.phase.summary.uniform.maxAbsPhaseDeltaDeg,
    report.phase.summary['non-uniform'].maxAbsPhaseDeltaDeg,
  );
  ui.phaseDelta.textContent = `${maxPhaseDeg.toFixed(4)}°`;
  const noInversion = report.checks.highFrequencyNoPhaseInversion;
  ui.phaseBadge.textContent = noInversion ? '高频无相位反转' : '检测到相位偏差';
  ui.phaseBadge.style.color = noInversion ? '#c9f06b' : '#ff7c86';

  ui.phaseTable.replaceChildren(...report.phase.rows.map((row) => {
    const tr = document.createElement('tr');
    const values = [
      `${(row.ratioOfNyquist * 100).toFixed(1)}%`,
      `${row.frequencyHz.toFixed(1)} Hz`,
      `${row.magnitudeDb.toFixed(2)} dB`,
      `${row.unwrappedPhaseRad.toFixed(3)} rad`,
      `${(row.modes.uniform.phaseDeltaRad * 180 / Math.PI).toExponential(2)}°`,
      `${(row.modes['non-uniform'].phaseDeltaRad * 180 / Math.PI).toExponential(2)}°`,
    ];
    for (const value of values) {
      const td = document.createElement('td');
      td.textContent = value;
      tr.append(td);
    }
    return tr;
  }));

  drawDifferenceChart(uniformOutput, nonUniformOutput);
  drawCpuChart(report.cpu.uniform, report.cpu.nonUniform);
  drawPhaseChart(report.phase);
}



