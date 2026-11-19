export const AES3 = Object.freeze({
  channels: ["A", "B"],
  channelLabels: { A: "L", B: "R" },
  blockFrames: 192,
  subframeSlots: 32,
  preambleSlots: 4,
  audioSlots: 24,
  paritySlot: 31
});

const PREAMBLES = {
  X: { prior0: "11100010", prior1: "00011101" },
  Y: { prior0: "11100100", prior1: "00011011" },
  Z: { prior0: "11101000", prior1: "00010111" }
};

const PREAMBLE_LOOKUP = new Map();
for (const [id, variants] of Object.entries(PREAMBLES)) {
  for (const pattern of Object.values(variants)) {
    PREAMBLE_LOOKUP.set(pattern, {
      id,
      channel: id === "Y" ? "B" : "A",
      inverted: pattern[0] === "0"
    });
  }
}

export function sampleBits(sample) {
  const word = sample & 0xffffff;
  return Array.from({ length: 24 }, (_, bit) => (word >> bit) & 1);
}

export function parityFor(bits) {
  return bits.reduce((parity, bit) => parity ^ bit, 0);
}

export function encodeSubframe({
  sample = 0,
  preamble = "X",
  priorState = 0,
  validity = false,
  user = false,
  channelStatus = false,
  flipSlot = null
}) {
  const audio = sampleBits(sample);
  const bits = [...audio, Number(validity), Number(user), Number(channelStatus)];
  const sourceParity = parityFor(bits);
  bits.push(sourceParity);
  if (flipSlot !== null && flipSlot >= 4 && flipSlot <= 27) {
    bits[flipSlot - 4] ^= 1;
  }

  const pattern = PREAMBLES[preamble][priorState ? "prior1" : "prior0"];
  const states = [...pattern].map(Number);
  let state = states[states.length - 1];
  for (const bit of bits) {
    const start = state ^ 1;
    const end = bit ? state : start;
    states.push(start, end);
    state = end;
  }

  return {
    preamble,
    channel: preamble === "Y" ? "B" : "A",
    states,
    bits,
    sourceParity,
    endState: states[states.length - 1],
    flipSlot
  };
}

export function statesToEdges(states, halfUnitNs) {
  if (!states.length) return [];
  const edges = [{ timeNs: 0, level: states[0] }];
  for (let index = 1; index < states.length; index += 1) {
    if (states[index] !== states[index - 1]) {
      edges.push({ timeNs: index * halfUnitNs, level: states[index] });
    }
  }
  return edges;
}

export function encodeAes3Stream({
  samples,
  sampleRate,
  leftStatus = new Uint8Array(24),
  rightStatus = new Uint8Array(24),
  faults = {}
}) {
  const bitClock = sampleRate * 64;
  const unitNs = 1e9 / bitClock;
  const halfUnitNs = unitNs / 2;
  const states = [];
  let priorState = 0;
  const reference = [];

  const flip = faults.bitFlip;
  for (let frame = 0; frame < samples.length; frame += 1) {
    const left = samples[frame].left;
    const right = samples[frame].right;
    const statusIndex = frame % AES3.blockFrames;
    for (const channel of AES3.channels) {
      const isLeft = channel === "A";
      const preamble = isLeft ? (frame % AES3.blockFrames === 0 ? "Z" : "X") : "Y";
      const statusByte = isLeft ? leftStatus : rightStatus;
      const cBit = (statusByte[statusIndex >> 3] >> (statusIndex & 7)) & 1;
      const flipSlot = flip && flip.frame === frame && flip.channel === channel
        ? flip.slot
        : null;
      const subframe = encodeSubframe({
        sample: isLeft ? left : right,
        preamble,
        priorState,
        validity: false,
        user: !isLeft,
        channelStatus: Boolean(cBit),
        flipSlot
      });
      states.push(...subframe.states);
      priorState = subframe.endState;
    }
    reference.push({ frame, left, right });
  }

  let edges = statesToEdges(states, halfUnitNs);
  if (faults.jitter) {
    const { start, end, amplitudeUI = 0.18 } = faults.jitter;
    const startNs = start * 1e9;
    const endNs = end * 1e9;
    edges = edges.map((edge) => {
      if (edge.timeNs < startNs || edge.timeNs > endNs) return edge;
      const norm = (edge.timeNs - startNs) / Math.max(1, endNs - startNs);
      const envelope = Math.sin(Math.PI * norm);
      const wobble =
        0.67 * Math.sin(norm * Math.PI * 18) +
        0.33 * Math.sin(norm * Math.PI * 47 + 0.8);
      return {
        ...edge,
        timeNs: edge.timeNs + wobble * envelope * amplitudeUI * unitNs
      };
    });
  }

  if (faults.dropout) {
    const startNs = faults.dropout.start * 1e9;
    const endNs = faults.dropout.end * 1e9;
    edges = edges.filter((edge) => edge.timeNs < startNs || edge.timeNs >= endNs);
  }

  edges.sort((a, b) => a.timeNs - b.timeNs);
  return {
    edges,
    reference,
    bitClock,
    sampleRate,
    unitNs,
    halfUnitNs,
    duration: samples.length / sampleRate
  };
}

export function parseEdgeCsv(text) {
  const lines = text.replace(/^\uFEFF/, "").split(/\r?\n/).filter((line) => line.trim());
  if (!lines.length) throw new Error("CSV 为空");
  const header = lines[0].toLowerCase().trim();
  const hasHeader = /time|timestamp/.test(header);
  const columns = hasHeader ? header.split(",").map((item) => item.trim()) : [];
  const timeColumn = columns.findIndex((item) => /time|timestamp/.test(item));
  const levelColumn = columns.findIndex((item) => /level|state|logical/.test(item));
  const timeScale = columns[timeColumn]?.includes("_s") && !columns[timeColumn]?.includes("_ns")
    ? 1e9
    : columns[timeColumn]?.includes("_us")
      ? 1e3
      : 1;
  const edges = [];
  for (let index = hasHeader ? 1 : 0; index < lines.length; index += 1) {
    const fields = lines[index].split(",").map((item) => item.trim());
    const time = Number(fields[timeColumn >= 0 ? timeColumn : 0]);
    const level = Number(fields[levelColumn >= 0 ? levelColumn : 1]);
    if (!Number.isFinite(time) || (level !== 0 && level !== 1)) {
      throw new Error(`CSV 第 ${index + 1} 行无法解析`);
    }
    edges.push({ timeNs: time * timeScale, level });
  }
  edges.sort((a, b) => a.timeNs - b.timeNs);
  return { edges, timeUnit: timeScale === 1 ? "ns" : "converted" };
}

export function edgeSequenceToCsv(edges) {
  const rows = ["time_ns,level"];
  for (const edge of edges) {
    rows.push(`${edge.timeNs.toFixed(6)},${edge.level}`);
  }
  return `${rows.join("\n")}\n`;
}

function median(values) {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2
    ? sorted[middle]
    : (sorted[middle - 1] + sorted[middle]) / 2;
}

function quantile(values, q) {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.floor((sorted.length - 1) * q))];
}

function wrap(value, period) {
  return value - Math.round(value / period) * period;
}

function estimateHalfUnit(edges) {
  const deltas = [];
  for (let index = 1; index < edges.length; index += 1) {
    const delta = edges[index].timeNs - edges[index - 1].timeNs;
    if (delta > 0) deltas.push(delta);
  }
  if (!deltas.length) throw new Error("至少需要两个边沿");
  const lower = quantile(deltas, 0.04);
  let base = median(deltas.filter((delta) => delta <= lower * 1.7));
  if (!(base > 0)) base = lower;
  let half = base;
  const useful = deltas.filter((delta) => delta <= half * 24);
  for (let pass = 0; pass < 5; pass += 1) {
    const adjusted = useful.map((delta) => {
      const units = Math.max(1, Math.round(delta / half));
      return delta / units;
    });
    const next = median(adjusted);
    if (!(next > 0) || Math.abs(next - half) < half * 1e-9) break;
    half = next;
  }
  return { half, base };
}

function estimatePhase(edges, half) {
  const origin = edges[0].timeNs;
  const offsets = edges.map((edge) => wrap(edge.timeNs - origin, half));
  return origin + median(offsets);
}

function buildSampledStates(edges, phase, half) {
  const last = edges[edges.length - 1].timeNs;
  const count = Math.max(0, Math.floor((last - phase) / half) + 2);
  const states = new Uint8Array(count);
  let edgeIndex = 0;
  let level = edges[0].level;
  for (let index = 0; index < count; index += 1) {
    const sampleTime = phase + (index + 0.5) * half;
    while (edgeIndex + 1 < edges.length && edges[edgeIndex + 1].timeNs <= sampleTime) {
      edgeIndex += 1;
      level = edges[edgeIndex].level;
    }
    states[index] = level;
  }
  return states;
}

function findPreambles(states, phase, half) {
  const preambles = [];
  for (let index = 0; index + 64 <= states.length; index += 1) {
    if (states[index] !== states[index + 1] && states[index] !== states[index + 2]) {
      continue;
    }
    let key = "";
    for (let offset = 0; offset < 8; offset += 1) key += states[index + offset];
    const meta = PREAMBLE_LOOKUP.get(key);
    if (!meta) continue;
    preambles.push({
      index,
      startNs: phase + index * half,
      id: meta.id,
      channel: meta.channel,
      inverted: meta.inverted
    });
  }
  return preambles;
}

function decodePreambleCandidate(states, preamble) {
  const dataStates = states.subarray(preamble.index + 8, preamble.index + 64);
  const bits = [];
  for (let index = 0; index < dataStates.length; index += 2) {
    bits.push(dataStates[index] !== dataStates[index + 1] ? 1 : 0);
  }
  let pcm = 0;
  for (let bit = 0; bit < 24; bit += 1) pcm |= bits[bit] << bit;
  if (pcm & 0x800000) pcm -= 0x1000000;
  const expectedParity = parityFor(bits.slice(0, 27));
  return {
    ...preamble,
    bits,
    pcm,
    validity: Boolean(bits[24]),
    user: bits[25],
    channelStatus: bits[26],
    parity: bits[27],
    parityError: expectedParity !== bits[27]
  };
}

function clockScore(edges, half) {
  const phase = estimatePhase(edges, half);
  const states = buildSampledStates(edges, phase, half);
  const preambles = findPreambles(states, phase, half);
  return { half, phase, states, preambles };
}

function recoverClock(edges) {
  const { half, base } = estimateHalfUnit(edges);
  const candidates = [...new Set([half, base, half / 2, base / 2])]
    .filter((value) => Number.isFinite(value) && value > 0);
  let best = null;
  for (const candidate of candidates) {
    try {
      const result = clockScore(edges, candidate);
      const score = result.preambles.length * 10 - Math.abs(candidate - base) / base;
      if (!best || score > best.score) best = { ...result, score };
    } catch {
      // A candidate that cannot produce a stable grid is discarded.
    }
  }
  if (!best || !best.preambles.length) {
    throw new Error("未找到有效的前导序列，请检查边沿极性和时钟");
  }
  return best;
}

function dedupePreambles(preambles, half) {
  const result = [];
  for (const preamble of [...preambles].sort((a, b) => a.startNs - b.startNs)) {
    const previous = result[result.length - 1];
    if (!previous || preamble.startNs - previous.startNs > half * 4) {
      result.push(preamble);
    }
  }
  return result;
}

function detectDropouts(edges, unitNs) {
  const gaps = [];
  for (let index = 1; index < edges.length; index += 1) {
    const gap = edges[index].timeNs - edges[index - 1].timeNs;
    if (gap > unitNs * 4) {
      gaps.push({
        startNs: edges[index - 1].timeNs,
        endNs: edges[index].timeNs,
        durationNs: gap,
        lostUI: gap / unitNs
      });
    }
  }
  return gaps;
}

function detectJitter(edges, phase, half, durationNs) {
  const samples = edges.map((edge) => ({
    timeNs: edge.timeNs,
    residualUI: wrap(edge.timeNs - phase, half) / (half * 2)
  }));
  const prefix = new Float64Array(samples.length + 1);
  for (let index = 0; index < samples.length; index += 1) {
    prefix[index + 1] = prefix[index] + samples[index].residualUI ** 2;
  }
  const baseNs = edges[0]?.timeNs || 0;
  const windowNs = 1e6;
  const stepNs = 0.5e6;
  const active = [];
  let left = 0;
  let right = 0;
  for (let start = 0; start < durationNs; start += stepNs) {
    const windowStart = baseNs + start;
    const end = windowStart + windowNs;
    while (left < samples.length && samples[left].timeNs < windowStart) left += 1;
    if (right < left) right = left;
    while (right < samples.length && samples[right].timeNs < end) right += 1;
    const count = right - left;
    if (count < 4) continue;
    const rms = Math.sqrt((prefix[right] - prefix[left]) / count);
    if (rms < 0.04) continue;
    active.push({ startNs: windowStart, endNs: Math.min(end, baseNs + durationNs), rms });
  }
  const merged = [];
  for (const window of active) {
    const previous = merged[merged.length - 1];
    if (previous && window.startNs <= previous.endNs + stepNs * 1.5) {
      previous.endNs = Math.max(previous.endNs, window.endNs);
      previous.rms = Math.max(previous.rms, window.rms);
    } else {
      merged.push({ ...window });
    }
  }
  return merged.map((window) => {
    const residuals = [];
    let index = 0;
    while (index < samples.length && samples[index].timeNs < window.startNs) index += 1;
    while (index < samples.length && samples[index].timeNs <= window.endNs) {
      residuals.push(samples[index].residualUI);
      index += 1;
    }
    const rms = Math.sqrt(residuals.reduce((sum, value) => sum + value ** 2, 0) / Math.max(1, residuals.length));
    const peak = residuals.reduce((max, value) => Math.max(max, Math.abs(value)), 0);
    return { ...window, rms, peak };
  });
}

function statusFromSubframes(subframes) {
  const bytes = new Uint8Array(24);
  let available = 0;
  for (const subframe of subframes) {
    const bitIndex = ((subframe.frameIndex % AES3.blockFrames) + AES3.blockFrames) % AES3.blockFrames;
    if (subframe.frameIndex >= 0) {
      bytes[bitIndex >> 3] |= subframe.channelStatus << (bitIndex & 7);
      available += 1;
    }
  }
  let label = "";
  for (let index = 20; index < 24; index += 1) {
    if (bytes[index] >= 32 && bytes[index] < 127) label += String.fromCharCode(bytes[index]);
  }
  return {
    available,
    bits: available,
    bytes: Array.from(bytes),
    hex: Array.from(bytes.slice(0, 8), (value) => value.toString(16).padStart(2, "0")).join(" "),
    label: label || (label ? "" : "—")
  };
}

function toSeconds(ns) {
  return Number((ns / 1e9).toFixed(9));
}

export function decodeEdges(inputEdges, options = {}) {
  const edges = inputEdges
    .map((edge) => ({ timeNs: Number(edge.timeNs ?? edge.time), level: Number(edge.level) }))
    .filter((edge) => Number.isFinite(edge.timeNs) && (edge.level === 0 || edge.level === 1))
    .sort((a, b) => a.timeNs - b.timeNs);
  if (edges.length < 64) throw new Error("边沿数量不足，无法恢复位时钟");

  const clock = options.halfUnitNs
    ? {
        half: options.halfUnitNs,
        phase: estimatePhase(edges, options.halfUnitNs),
        states: buildSampledStates(edges, estimatePhase(edges, options.halfUnitNs), options.halfUnitNs)
      }
    : recoverClock(edges);
  const { half, phase, states } = clock;
  const preambles = dedupePreambles(
    clock.preambles || findPreambles(states, phase, half),
    half
  ).map((preamble) => decodePreambleCandidate(states, preamble));
  if (!preambles.length) throw new Error("未找到 X/Y/Z 前导序列");

  const firstA = preambles.find((subframe) => subframe.channel === "A");
  if (!firstA) throw new Error("未找到 A 声道前导序列");
  const originNs = firstA.startNs;
  const framePeriodNs = 128 * half;
  const lastEdgeNs = edges[edges.length - 1].timeNs;
  const totalFrameSlots = Math.max(1, Math.min(1000000, Math.ceil((lastEdgeNs - originNs) / framePeriodNs)));
  const frames = Array.from({ length: totalFrameSlots }, (_, frameIndex) => ({
    frameIndex,
    left: 0,
    right: 0,
    leftPresent: false,
    rightPresent: false,
    leftValidity: false,
    rightValidity: false
  }));
  const subframes = [];

  for (const subframe of preambles) {
    const frameIndex = Math.floor((subframe.startNs - originNs) / framePeriodNs);
    if (frameIndex < 0 || frameIndex >= totalFrameSlots) continue;
    const frame = frames[frameIndex];
    const isLeft = subframe.channel === "A";
    if (isLeft && !frame.leftPresent) {
      frame.left = subframe.pcm;
      frame.leftPresent = true;
      frame.leftValidity = subframe.validity;
    } else if (!isLeft && !frame.rightPresent) {
      frame.right = subframe.pcm;
      frame.rightPresent = true;
      frame.rightValidity = subframe.validity;
    }
    subframes.push({
      ...subframe,
      frameIndex,
      timeNs: subframe.startNs,
      time: toSeconds(subframe.startNs),
      channelLabel: AES3.channelLabels[subframe.channel]
    });
  }

  subframes.sort((a, b) => a.startNs - b.startNs);
  const dropouts = detectDropouts(edges, half * 2);
  const jitter = detectJitter(edges, phase, half, lastEdgeNs - edges[0].timeNs);
  const parityFailures = subframes.filter((subframe) => subframe.parityError);
  const errors = [];

  for (const event of jitter) {
    errors.push({
      type: "CLOCK_JITTER",
      severity: "warning",
      time_s: toSeconds(event.startNs),
      end_s: toSeconds(event.endNs),
      frame: null,
      channel: null,
      title: "位时钟抖动超限",
      detail: `窗口 RMS ${event.rms.toFixed(3)} UI，峰值 ${event.peak.toFixed(3)} UI`,
      value: Number(event.rms.toFixed(6)),
      unit: "UI RMS"
    });
  }
  for (const subframe of parityFailures) {
    errors.push({
      type: "PARITY",
      severity: "error",
      time_s: toSeconds(subframe.startNs),
      end_s: null,
      frame: subframe.frameIndex,
      channel: subframe.channelLabel,
      title: "子帧奇偶校验失败",
      detail: `${subframe.id} 前导，${subframe.channelLabel} 声道，采样字 0x${(subframe.pcm & 0xffffff).toString(16).padStart(6, "0")} 可能被反转位污染`,
      value: subframe.pcm,
      unit: "PCM"
    });
  }
  for (const event of dropouts) {
    errors.push({
      type: "DROPOUT",
      severity: "error",
      time_s: toSeconds(event.startNs),
      end_s: toSeconds(event.endNs),
      frame: null,
      channel: null,
      title: "边沿断流 / 失锁",
      detail: `缺失约 ${Math.round(event.lostUI)} UI（${(event.durationNs / 1e6).toFixed(3)} ms）`,
      value: Number(event.lostUI.toFixed(2)),
      unit: "UI"
    });
  }
  errors.sort((a, b) => a.time_s - b.time_s || a.type.localeCompare(b.type));
  errors.forEach((error, index) => {
    error.id = `E${String(index + 1).padStart(3, "0")}`;
  });

  const completeFrames = frames.filter((frame) => frame.leftPresent && frame.rightPresent);
  const partialFrames = frames.filter((frame) => frame.leftPresent !== frame.rightPresent);
  const totalDurationNs = Math.max(1, lastEdgeNs - edges[0].timeNs);
  const dropoutNs = dropouts.reduce((sum, event) => sum + event.durationNs, 0);
  const leftStatus = statusFromSubframes(subframes.filter((subframe) => subframe.channel === "A"));
  const rightStatus = statusFromSubframes(subframes.filter((subframe) => subframe.channel === "B"));
  const bitClock = 1e9 / (half * 2);
  const jitterRms = jitter.length ? Math.max(...jitter.map((event) => event.rms)) : 0;
  const maxJitter = edges.reduce((max, edge) => {
    const residual = Math.abs(wrap(edge.timeNs - phase, half) / (half * 2));
    return Math.max(max, residual);
  }, 0);

  return {
    edges,
    frames,
    subframes,
    errors,
    dropouts,
    jitter,
    phaseNs: phase,
    halfUnitNs: half,
    unitNs: half * 2,
    originNs,
    framePeriodNs,
    channelStatus: { A: leftStatus, B: rightStatus },
    summary: {
      edgeCount: edges.length,
      subframeCount: subframes.length,
      frameCount: completeFrames.length,
      totalFrameSlots,
      incompleteFrames: partialFrames.length,
      lostFrames: totalFrameSlots - completeFrames.length - partialFrames.length,
      bitClock,
      sampleRate: bitClock / 64,
      duration: (lastEdgeNs - edges[0].timeNs) / 1e9,
      lockPercent: Math.max(0, Math.min(100, 100 * (1 - dropoutNs / totalDurationNs))),
      parityErrorCount: parityFailures.length,
      dropoutCount: dropouts.length,
      jitterCount: jitter.length,
      jitterRmsUI: jitterRms,
      maxJitterUI: maxJitter,
      invalidSamples: subframes.filter((subframe) => subframe.validity).length,
      channelOrder: { A: "L / 左", B: "R / 右" }
    }
  };
}
