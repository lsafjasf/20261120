"use strict";

const TAU = Math.PI * 2;
const wrapAngle = (a) => ((a + Math.PI) % TAU + TAU) % TAU - Math.PI;
const angleDiff = (a, b) => wrapAngle(a - b);
const alpha = (fc, dt) => 1 / (1 + 1 / (TAU * fc * dt));

class OneEuroFilter {
  constructor(minCutoff = 1.0, beta = 0.6, dCutoff = 1.0) {
    this.minCutoff = minCutoff;
    this.beta = beta;
    this.dCutoff = dCutoff;
    this.reset();
  }
  reset() {
    this.tPrev = null;
    this.xPrev = null;
    this.dxPrev = 0;
    this.lastCutoff = this.minCutoff;
    this.lastVelocity = 0;
  }
  filter(t, x) {
    x = wrapAngle(x);
    if (this.tPrev === null) { this.tPrev = t; this.xPrev = x; return x; }
    const dt = Math.max(t - this.tPrev, 1e-4);
    const dx = angleDiff(x, this.xPrev) / dt;
    const aD = alpha(this.dCutoff, dt);
    const dxHat = aD * dx + (1 - aD) * this.dxPrev;
    this.lastCutoff = this.minCutoff + this.beta * Math.abs(dxHat);
    const a = alpha(this.lastCutoff, dt);
    const xHat = wrapAngle(this.xPrev + a * angleDiff(x, this.xPrev));
    this.tPrev = t; this.xPrev = xHat; this.dxPrev = dxHat;
    this.lastVelocity = dxHat;
    return xHat;
  }
}

class SlewLimiter {
  constructor(maxRatePerSec, initial = 1) {
    this.maxRate = maxRatePerSec;
    this.value = initial;
    this.tPrev = null;
  }
  update(t, target) {
    const dt = this.tPrev === null ? 1 / 120 : Math.max(t - this.tPrev, 0);
    this.tPrev = t;
    let delta = target - this.value;
    const limit = this.maxRate * dt;
    delta = Math.max(-limit, Math.min(limit, delta));
    this.value += delta;
    return this.value;
  }
}

const equalPowerGains = (az) => {
  const pan = Math.max(-1, Math.min(1, az / (Math.PI / 2)));
  const ang = pan * Math.PI / 4;
  return [Math.cos(ang), Math.sin(ang)];
};

const dopplerRatio = (omega, distance, az, c = 343) => {
  const vr = -omega * distance * Math.sin(az);
  return c / (c - vr);
};

class LatencyStats {
  constructor() { this.samples = []; }
  record(captureMs, applyMs) { this.samples.push(applyMs - captureMs); }
  summary() {
    if (!this.samples.length) return { p50: 0, p95: 0, max: 0 };
    const s = [...this.samples].sort((a, b) => a - b);
    const p = (q) => s[Math.min(s.length - 1, Math.floor(q * s.length))];
    return { p50: p(0.5), p95: p(0.95), max: s[s.length - 1] };
  }
  reset() { this.samples = []; }
}

const state = {
  audio: null,
  osc: null,
  pan: null,
  gain: null,
  running: false,
  rawYaw: 0,
  yaw: { direct: 0, smooth: 0 },
  filters: {
    direct: { filter: null, call: (t, x) => wrapAngle(x) },
    smooth: new OneEuroFilter(),
  },
  slew: { direct: new SlewLimiter(1e9), smooth: new SlewLimiter(1.5) },
  queue: [],
  lat: { direct: new LatencyStats(), smooth: new LatencyStats() },
  sensorTimes: [],
  renderTimes: [],
  dopplerMax: { direct: 0, smooth: 0 },
  prevCents: { direct: null, smooth: null },
  prevYaw: { direct: null, smooth: null },
  yawHistory: { direct: [], smooth: [] },
  lag: { direct: 0, smooth: 0 },
  simTimer: null,
};
