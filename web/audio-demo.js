"use strict";

// Browser-side mirror of src/physics.js (ISO 9613-1) and src/airFilter.js,
// implemented with the real Web Audio node graph for listening verification.

const SPEED_OF_SOUND = 343.2;
const SHELF_FREQS = [250, 500, 1000, 2000, 4000, 8000, 12000, 16000];

function airAbsorptionCoefficient(frequency, temperatureK, pressureKpa, humidity) {
  const T0 = 293.15;
  const T01 = 273.16;
  const pRef = 101.325;
  const T = temperatureK;
  const p = pressureKpa;
  const C = -6.8346 * Math.pow(T01 / T, 1.261) + 4.6151;
  const pSatOverPRef = Math.pow(10, C);
  const hMol = humidity * pSatOverPRef * (pRef / p);
  const frO =
    (p / pRef) *
    (24.0 + (4.04e4 * hMol * (0.02 + hMol)) / (0.391 + hMol));
  const frN =
    (p / pRef) *
    Math.pow(T / T0, -0.5) *
    (9 +
      280 *
        hMol *
        Math.exp(-4.17 * (Math.pow(T / T0, -1 / 3) - 1)));
  const f = frequency;
  return (
    8.686 *
    f *
    f *
    (1.84e-11 * Math.pow(T / T0, 0.5) * (pRef / p) +
      Math.pow(T / T0, -2.5) *
        ((0.01275 * Math.exp(-2239.1 / T)) / (frO + (f * f) / frO) +
          (0.1068 * Math.exp(-3352 / T)) / (frN + (f * f) / frN)))
  );
}

// RBJ high-shelf coefficient set, returned as the b/a arrays used by
// BiquadFilterType "highshelf" semantics (frequency + gain only).
function solveShelfGains(targetDb, evalFreqs, shelfFreqs, sampleRate) {
  const basis = shelfFreqs.map((f0) =>
    evalFreqs.map((f) => shelfResponseDb(f0, 6, f, sampleRate)),
  );
  const k = shelfFreqs.length;
  const m = evalFreqs.length;
  const normal = Array.from({ length: k }, () => new Array(k).fill(0));
  const rhs = new Array(k).fill(0);
  for (let i = 0; i < m; i++) {
    const row = basis.map((col) => col[i] / 6);
    for (let p = 0; p < k; p++) {
      rhs[p] += row[p] * targetDb[i];
      for (let q = 0; q < k; q++) normal[p][q] += row[p] * row[q];
    }
  }
  return gaussianSolve(normal, rhs).map((g) => Math.min(0, g));
}

// Magnitude response of an RBJ high-shelf tuned at shelfFreq, evaluated at
// freq. The coefficient alpha uses the shelf's own center frequency, while
// the polynomial evaluation uses the evaluation frequency (standard RBJ).
function shelfResponseDb(shelfFreq, gainDb, freq, sampleRate) {
  const A = Math.pow(10, gainDb / 40);
  const w0 = (2 * Math.PI * shelfFreq) / sampleRate;
  const w = (2 * Math.PI * freq) / sampleRate;
  const cosW = Math.cos(w);
  const alpha = (Math.sin(w0) / 2) * Math.sqrt(2);
  const twoSqrtAAlpha = 2 * Math.sqrt(A) * alpha;
  const Ap1 = A + 1;
  const Am1 = A - 1;
  const b = [
    A * (Ap1 + Am1 * Math.cos(w0) + twoSqrtAAlpha),
    -2 * A * (Am1 + Ap1 * Math.cos(w0)),
    A * (Ap1 + Am1 * Math.cos(w0) - twoSqrtAAlpha),
  ];
  const a = [
    Ap1 - Am1 * Math.cos(w0) + twoSqrtAAlpha,
    2 * (Am1 - Ap1 * Math.cos(w0)),
    Ap1 - Am1 * Math.cos(w0) - twoSqrtAAlpha,
  ];
  const num =
    b[0] * b[0] +
    b[1] * b[1] +
    b[2] * b[2] +
    2 * (b[0] * b[1] + b[1] * b[2]) * cosW +
    2 * b[0] * b[2] * Math.cos(2 * w);
  const den =
    a[0] * a[0] +
    a[1] * a[1] +
    a[2] * a[2] +
    2 * a[1] * (a[0] + a[2]) * cosW +
    2 * a[0] * a[2] * Math.cos(2 * w);
  return 10 * Math.log10(num / den);
}

function gaussianSolve(matrix, rhs) {
  const a = matrix.map((row, i) => [...row, rhs[i]]);
  const n = a.length;
  for (let col = 0; col < n; col++) {
    let pivot = col;
    for (let r = col + 1; r < n; r++) {
      if (Math.abs(a[r][col]) > Math.abs(a[pivot][col])) pivot = r;
    }
    [a[col], a[pivot]] = [a[pivot], a[col]];
    const divisor = a[col][col] || 1e-18;
    for (let c = col; c <= n; c++) a[col][c] /= divisor;
    for (let r = 0; r < n; r++) {
      if (r === col) continue;
      const factor = a[r][col];
      for (let c = col; c <= n; c++) a[r][c] -= factor * a[col][c];
    }
  }
  return a.map((row) => row[n]);
}

function makeImpulseResponse(audioContext, predelaySec, rt60) {
  const rate = audioContext.sampleRate;
  const predelay = Math.max(1, Math.round(predelaySec * rate));
  const tail = Math.ceil(rt60 * rate);
  const length = predelay + tail;
  const ir = audioContext.createBuffer(2, length, rate);
  const tau = rt60 / Math.log(1000);
  for (let channel = 0; channel < 2; channel++) {
    const data = ir.getChannelData(channel);
    for (let i = 0; i < tail; i++) {
      const t = i / rate;
      const early =
        i === 0 ? 1 :
        i === Math.round(0.012 * rate) ? 0.55 :
        i === Math.round(0.027 * rate) ? 0.4 : 0;
      data[predelay + i] =
        (early + 0.22 * (Math.random() * 2 - 1)) * Math.exp(-t / tau);
    }
  }
  return ir;
}

const state = {
  ctx: null,
  source: null,
  panner: null,
  distanceGain: null,
  shelves: [],
  wetGain: null,
  dryGain: null,
  convolver: null,
  analyser: null,
  playing: false,
  rafId: null,
};

const ui = {
  distance: document.getElementById("distance"),
  humidity: document.getElementById("humidity"),
  rt60: document.getElementById("rt60"),
  distanceValue: document.getElementById("distanceValue"),
  humidityValue: document.getElementById("humidityValue"),
  rt60Value: document.getElementById("rt60Value"),
  status: document.getElementById("status"),
  metrics: document.getElementById("metrics"),
  canvas: document.getElementById("spectrum"),
};

function makePinkNoiseBuffer(ctx) {
  const length = ctx.sampleRate * 4;
  const buffer = ctx.createBuffer(1, length, ctx.sampleRate);
  const data = buffer.getChannelData(0);
  let b0 = 0, b1 = 0, b2 = 0, b3 = 0, b4 = 0, b5 = 0, b6 = 0;
  for (let i = 0; i < length; i++) {
    const white = Math.random() * 2 - 1;
    b0 = 0.99886 * b0 + white * 0.0555179;
    b1 = 0.99332 * b1 + white * 0.0750759;
    b2 = 0.969 * b2 + white * 0.153852;
    b3 = 0.8665 * b3 + white * 0.3104856;
    b4 = 0.55 * b4 + white * 0.5329522;
    b5 = -0.7616 * b5 - white * 0.016898;
    data[i] = (b0 + b1 + b2 + b3 + b4 + b5 + b6 + white * 0.5362) * 0.11;
    b6 = white * 0.115926;
  }
  return buffer;
}

async function buildGraph() {
  const ctx = new AudioContext();
  state.ctx = ctx;
  const source = ctx.createBufferSource();
  source.buffer = makePinkNoiseBuffer(ctx);
  source.loop = true;

  // Measured HRTF set baked into the browser (spec uses built-in HRIR data).
  const panner = ctx.createPanner();
  panner.panningModel = "HRTF";
  panner.distanceModel = "inverse";
  panner.refDistance = 1;
  panner.maxDistance = 100;
  panner.rolloffFactor = 1;

  state.distanceGain = ctx.createGain();
  state.shelves = SHELF_FREQS.map(() => {
    const filter = ctx.createBiquadFilter();
    filter.type = "highshelf";
    return filter;
  });

  state.dryGain = ctx.createGain();
  state.wetGain = ctx.createGain();
  state.convolver = ctx.createConvolver();
  state.analyser = ctx.createAnalyser();
  state.analyser.fftSize = 4096;

  let node = source;
  node.connect(panner);
  panner.connect(state.distanceGain);
  node = state.distanceGain;
  for (const shelf of state.shelves) {
    node.connect(shelf);
    node = shelf;
  }
  node.connect(state.dryGain);
  node.connect(state.convolver);
  state.dryGain.connect(state.analyser);
  state.wetGain.connect(state.analyser);
  state.convolver.connect(state.wetGain);
  state.analyser.connect(ctx.destination);

  state.panner = panner;
  state.source = source;
  source.start();
  await ctx.resume();
  state.playing = true;
  updateParameters();
  drawSpectrum();
}

function currentSettings() {
  return {
    distance: Number(ui.distance.value),
    humidity: Number(ui.humidity.value),
    rt60: Number(ui.rt60.value),
  };
}

function updateParameters() {
  const { distance, humidity, rt60 } = currentSettings();
  ui.distanceValue.textContent = `${distance.toFixed(1)} m`;
  ui.humidityValue.textContent = `${humidity} %RH`;
  ui.rt60Value.textContent = `${rt60.toFixed(2)} s`;
  if (!state.ctx) return;

  const ctx = state.ctx;
  const rate = ctx.sampleRate;
  state.panner.positionX.setValueAtTime(distance, ctx.currentTime);
  state.panner.positionY.setValueAtTime(0, ctx.currentTime);
  state.panner.positionZ.setValueAtTime(0, ctx.currentTime);

  const distanceDb = -20 * Math.log10(distance);
  state.distanceGain.gain.setValueAtTime(
    Math.pow(10, distanceDb / 20),
    ctx.currentTime,
  );

  const evalFreqs = Array.from(
    { length: 120 },
    (_, i) => 30 * Math.pow(18000 / 30, i / 119),
  );
  const target = evalFreqs.map(
    (f) => -airAbsorptionCoefficient(f, 293.15, 101.325, humidity) * distance,
  );
  const gains = solveShelfGains(target, evalFreqs, SHELF_FREQS, rate);
  state.shelves.forEach((shelf, i) => {
    shelf.frequency.setValueAtTime(SHELF_FREQS[i], ctx.currentTime);
    shelf.gain.setValueAtTime(gains[i], ctx.currentTime);
  });

  const predelay = 0.02 + distance / SPEED_OF_SOUND;
  state.convolver.buffer = makeImpulseResponse(ctx, predelay, rt60);
  state.wetGain.gain.setValueAtTime(0.55, ctx.currentTime);
  state.dryGain.gain.setValueAtTime(1, ctx.currentTime);

  const loss8k =
    airAbsorptionCoefficient(8000, 293.15, 101.325, humidity) * distance;
  const loss16k =
    airAbsorptionCoefficient(16000, 293.15, 101.325, humidity) * distance;
  ui.metrics.innerHTML = [
    ["距离律增益", `${distanceDb.toFixed(2)} dB`, "−20 log₁₀(d / 1 m)"],
    ["传播延迟", `${(distance / SPEED_OF_SOUND * 1000).toFixed(2)} ms`, "d / c"],
    ["混响预延迟", `${(predelay * 1000).toFixed(2)} ms`, "20 ms 基础 + 传播延迟"],
    ["8 kHz 空气损失", `${loss8k.toFixed(2)} dB`, "ISO 9613-1"],
    ["16 kHz 空气损失", `${loss16k.toFixed(2)} dB`, "高频衰减随距离快速增大"],
    ["搁架总增益", `${gains.reduce((a, b) => a + b, 0).toFixed(2)} dB`, "8 级 highshelf 拟合"],
  ]
    .map(
      (row) =>
        `<tr><td>${row[0]}</td><td>${row[1]}</td><td class="note">${row[2]}</td></tr>`,
    )
    .join("");
}

function drawSpectrum() {
  const canvas = ui.canvas;
  const ctx2d = canvas.getContext("2d");
  const data = new Uint8Array(state.analyser.frequencyBinCount);
  const render = () => {
    state.analyser.getByteFrequencyData(data);
    const w = canvas.width;
    const h = canvas.height;
    ctx2d.fillStyle = "#fff";
    ctx2d.fillRect(0, 0, w, h);
    ctx2d.strokeStyle = "#2563eb";
    ctx2d.lineWidth = 2;
    ctx2d.beginPath();
    const rate = state.ctx.sampleRate;
    const minF = 30;
    const maxF = rate / 2;
    for (let x = 0; x < w; x++) {
      const f = minF * Math.pow(maxF / minF, x / w);
      const bin = Math.round((f / rate) * state.analyser.fftSize);
      const db = (data[bin] / 255) * 240 - 120;
      const y = h - ((db + 100) / 90) * h;
      if (x === 0) ctx2d.moveTo(x, y);
      else ctx2d.lineTo(x, y);
    }
    ctx2d.stroke();
    ctx2d.fillStyle = "#64748b";
    ctx2d.font = "12px sans-serif";
    ctx2d.fillText("30 Hz", 6, h - 6);
    ctx2d.fillText(`${Math.round(maxF / 1000)} kHz`, w - 48, h - 6);
    ctx2d.fillText("dB", 6, 14);
    state.rafId = requestAnimationFrame(render);
  };
  render();
}

document.getElementById("play").addEventListener("click", async () => {
  if (state.playing) {
    await state.ctx.resume();
    return;
  }
  ui.status.textContent = "正在构建 Web Audio 节点图…";
  await buildGraph();
  ui.status.textContent = "播放中：拖动距离/湿度/RT60 滑块实时对比";
});

document.getElementById("stop").addEventListener("click", () => {
  if (state.ctx) {
    state.ctx.close();
    cancelAnimationFrame(state.rafId);
    Object.assign(state, { ctx: null, playing: false, shelves: [] });
    ui.status.textContent = "已停止";
  }
});

document.getElementById("sweep").addEventListener("click", async () => {
  if (!state.playing) await buildGraph();
  ui.distance.value = 1;
  updateParameters();
  ui.status.textContent = "A/B 对比：1 m（近）…";
  setTimeout(() => {
    ui.distance.value = 32;
    updateParameters();
    ui.status.textContent = "A/B 对比：32 m（远，高频明显变暗、响度降低）";
  }, 2500);
});

for (const input of [ui.distance, ui.humidity, ui.rt60]) {
  input.addEventListener("input", updateParameters);
}
