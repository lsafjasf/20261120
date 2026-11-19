"use strict";

const $ = (id) => document.getElementById(id);
const logEl = $("log");
const toRad = (deg) => deg * Math.PI / 180;
const log = (msg) => {
  const line = document.createElement("div");
  line.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
  logEl.appendChild(line);
  logEl.scrollTop = logEl.scrollHeight;
};

async function startAudio() {
  if (state.running) return;
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  const osc = ctx.createOscillator();
  osc.type = "sine";
  osc.frequency.value = 440;
  const gain = ctx.createGain();
  gain.gain.value = 0.12;
  const pan = ctx.createStereoPanner ? ctx.createStereoPanner() : null;
  osc.connect(gain);
  if (pan) gain.connect(pan), pan.connect(ctx.destination);
  else gain.connect(ctx.destination);
  osc.start();
  Object.assign(state, {
    audio: ctx, osc, gain, pan, running: true,
  });
  state.filters.smooth = new OneEuroFilter(
    parseFloat($("minCut").value), parseFloat($("beta").value));
  $("statusPill").textContent = "音频运行中";
  $("statusPill").className = "pill";
  log("AudioContext 已启动，440 Hz 测试音");
  requestAnimationFrame(renderLoop);
  setInterval(updateMetrics, 1000);
}

function pushOrientation(yawRad, captureMs) {
  state.rawYaw = yawRad;
  state.queue.push({ t: captureMs, yaw: yawRad });
  state.sensorTimes.push(captureMs);
  if (state.sensorTimes.length > 200) state.sensorTimes.shift();
}

async function enableSensors() {
  if (typeof DeviceOrientationEvent === "undefined") {
    log("当前浏览器不支持 DeviceOrientation，请使用模拟转头");
    $("statusPill").textContent = "无传感器";
    $("statusPill").className = "pill warn";
    return;
  }
  if (typeof DeviceOrientationEvent.requestPermission === "function") {
    try { await DeviceOrientationEvent.requestPermission(); }
    catch (err) { log("传感器权限被拒绝：" + err.message); return; }
  }
  window.addEventListener("deviceorientationabsolute", handleOrient, true);
  window.addEventListener("deviceorientation", handleOrient, true);
  log("已订阅 deviceorientation（alpha 罗盘角作为 yaw）");
}

function handleOrient(ev) {
  const alpha = ev.alpha;
  if (alpha == null) return;
  pushOrientation(-toRad(alpha), performance.now());
}

function simulateFastTurn() {
  if (state.simTimer) clearInterval(state.simTimer);
  const start = performance.now();
  const t0 = 600;
  const total = toRad(60);
  const sigma = total / (toRad(420) * Math.sqrt(2 * Math.PI));
  let yaw = toRad(-30);
  let prevT = 0;
  state.simTimer = setInterval(() => {
    const t = performance.now() - start;
    const z = (t - t0) / (sigma * 1000);
    const v = (total / (sigma * Math.sqrt(2 * Math.PI)))
      * Math.exp(-0.5 * z * z) / 1000;
    yaw += v * Math.max((t - prevT) / 1000, 0);
    prevT = t;
    const noise = (Math.random() - 0.5) * toRad(1.6);
    pushOrientation(yaw + noise, performance.now());
    if (t > 1800) clearInterval(state.simTimer);
  }, 1000 / 60);
  log("模拟 420°/s 峰值快速转头（60 Hz 事件 + 传感器噪声）");
}

let lastRenderMs = null;
function renderLoop(now) {
  if (!state.running) return;
  state.renderTimes.push(now);
  if (state.renderTimes.length > 200) state.renderTimes.shift();
  const dt = lastRenderMs ? Math.max((now - lastRenderMs) / 1000, 1e-4)
                          : 1 / 120;

  let applied = null;
  while (state.queue.length && state.queue[0].t <= now) {
    applied = state.queue.shift();
    state.yaw.direct = state.filters.direct.call(now / 1000, applied.yaw);
    state.yaw.smooth = state.filters.smooth.filter(now / 1000, applied.yaw);
    state.lat.direct.record(applied.t, now);
    state.lat.smooth.record(applied.t, now);
  }
  const mode = $("modeSel").value;
  state.prevYaw = state.prevYaw || { direct: null, smooth: null };
  for (const m of ["direct", "smooth"]) {
    const yawM = state.yaw[m];
    const azM = wrapAngle(0 - yawM);
    const prev = state.prevYaw[m] ?? yawM;
    const omega = angleDiff(yawM, prev) / dt;
    state.prevYaw[m] = yawM;
    const target = dopplerRatio(omega, 1, azM);
    const ratioM = state.slew[m].update(now / 1000, target);
    const cents = 1200 * Math.log2(ratioM);
    if (state.prevCents[m] != null) {
      state.dopplerMax[m] = Math.max(
        state.dopplerMax[m], Math.abs(cents - state.prevCents[m]));
    }
    state.prevCents[m] = cents;
  }

  const yaw = state.yaw[mode];
  const az = wrapAngle(0 - yaw);
  const [gl, gr] = equalPowerGains(az);
  const ratio = state.slew[mode].value;
  for (const m of ["direct", "smooth"]) {
    state.yawHistory[m].push(state.yaw[m]);
    if (state.yawHistory[m].length > 120) state.yawHistory[m].shift();
  }
  // Tracking lag: best integer-frame alignment of smoothed vs direct yaw.
  const d = state.yawHistory.direct;
  const sm = state.yawHistory.smooth;
  if (sm.length > 20) {
    let bestEnergy = Infinity, bestShift = 0;
    for (let shift = 0; shift < 12; shift++) {
      let sum = 0, n = 0;
      for (let i = shift; i < sm.length; i++) {
        sum += angleDiff(sm[i], d[i - shift]) ** 2;
        n++;
      }
      const energy = sum / Math.max(n, 1);
      if (energy < bestEnergy) { bestEnergy = energy; bestShift = shift; }
    }
    state.lag.smooth = bestShift * (1000 / 60);
  }

  state.osc.frequency.setTargetAtTime(440 * ratio, state.audio.currentTime, 0);
  if (state.pan) state.pan.pan.setTargetAtTime(gr - gl, state.audio.currentTime, 0);
  updateStage(yaw, az);
  lastRenderMs = now;
  requestAnimationFrame(renderLoop);
}

function updateStage(yaw, az) {
  const src = $("source");
  const r = 110;
  src.style.left = `calc(50% + ${Math.sin(az) * r}px)`;
  src.style.top = `calc(50% - ${Math.cos(az) * r}px)`;
  $("headDir").style.transform = `rotate(${yaw}rad)`;
}

function rateOf(times) {
  if (times.length < 2) return 0;
  return (times.length - 1) / ((times[times.length - 1] - times[0]) / 1000);
}

function updateMetrics() {
  $("sensorRate").textContent = rateOf(state.sensorTimes).toFixed(1);
  $("renderRate").textContent = rateOf(state.renderTimes).toFixed(1);
  const std = (arr) => {
    if (arr.length < 2) return 0;
    const mean = arr.reduce((a, b) => a + b, 0) / arr.length;
    return Math.sqrt(arr.reduce((a, b) => a + (b - mean) ** 2, 0)
                     / (arr.length - 1));
  };
  for (const mode of ["direct", "smooth"]) {
    const cap = mode === "direct" ? "Direct" : "Smooth";
    const s = state.lat[mode].summary();
    $(`l${cap}`).textContent = `${s.p50.toFixed(1)} / ${s.p95.toFixed(1)}`;
    $(`d${cap}`).textContent = state.dopplerMax[mode].toFixed(1);
    $(`j${cap}`).textContent =
      `${(std(state.yawHistory[mode]) * 180 / Math.PI).toFixed(2)}°`;
    $(`lag${cap}`).textContent =
      mode === "direct" ? "0" : state.lag.smooth.toFixed(0);
  }
}

$("startBtn").addEventListener("click", startAudio);
$("sensorBtn").addEventListener("click", enableSensors);
$("simBtn").addEventListener("click", simulateFastTurn);
$("minCut").addEventListener("input", (e) => {
  $("mcVal").textContent = `${e.target.value} Hz`;
  if (state.filters.smooth) state.filters.smooth.minCutoff = parseFloat(e.target.value);
});
$("beta").addEventListener("input", (e) => {
  $("betaVal").textContent = parseFloat(e.target.value).toFixed(2);
  if (state.filters.smooth) state.filters.smooth.beta = parseFloat(e.target.value);
});
