import { mkdir, writeFile } from "node:fs/promises";
import {
  AES3,
  decodeEdges,
  edgeSequenceToCsv,
  encodeAes3Stream
} from "../src/aes3.js";
import { encodeWav24, errorsToCsv } from "../src/export.js";

const SAMPLE_RATE = 8000;
const FRAMES = 256;
const TONES = { left: 1000, right: 1500 };
const OUTPUT = new URL("../data/scenarios/", import.meta.url);

const samples = Array.from({ length: FRAMES }, (_, frame) => ({
  left: Math.round(7_000_000 * Math.sin((2 * Math.PI * TONES.left * frame) / SAMPLE_RATE)),
  right: Math.round(7_000_000 * Math.cos((2 * Math.PI * TONES.right * frame) / SAMPLE_RATE))
}));

function statusBlock(label) {
  const bytes = new Uint8Array(24);
  bytes[0] = 0x01;
  bytes[1] = 0x02;
  bytes[20] = label.charCodeAt(0);
  bytes[21] = 0x08;
  return bytes;
}

const leftStatus = statusBlock("L");
const rightStatus = statusBlock("R");
const jitter = { start: 0.00768, end: 0.01216, amplitudeUI: 0.18 };
const bitFlip = { frame: 143, channel: "A", slot: 16 };
const dropout = { start: 0.02176, end: 0.02432 };
const definitions = {
  clean: { title: "干净链路", faults: {} },
  jitter: { title: "时钟抖动", faults: { jitter } },
  invert: { title: "反转位", faults: { bitFlip } },
  dropout: { title: "断流失锁", faults: { dropout } },
  composite: {
    title: "复合故障",
    faults: { jitter, bitFlip, dropout }
  }
};

function countMismatches(decoded, skip = new Set()) {
  const mismatches = [];
  let compared = 0;
  for (const frame of decoded.frames) {
    if (!frame.leftPresent || !frame.rightPresent || skip.has(frame.frameIndex)) continue;
    compared += 1;
    const expected = samples[frame.frameIndex];
    if (!expected || frame.left !== expected.left || frame.right !== expected.right) {
      mismatches.push({
        frame: frame.frameIndex,
        expected: expected || null,
        decoded: { left: frame.left, right: frame.right }
      });
    }
  }
  return { compared, mismatches };
}

function relockCheck(decoded) {
  let compared = 0;
  let swapped = 0;
  for (const frame of decoded.frames) {
    if (frame.frameIndex < 200 || !frame.leftPresent || !frame.rightPresent) continue;
    compared += 1;
    const expected = samples[frame.frameIndex];
    if (frame.left !== expected.left || frame.right !== expected.right) swapped += 1;
  }
  return { compared, swapped };
}

function insideEvent(decodedEvents, target, tolerance = 0.001) {
  return decodedEvents.find((event) => {
    const start = event.startNs / 1e9;
    const end = event.endNs / 1e9;
    return start <= target.end + tolerance && end >= target.start - tolerance;
  });
}

function validate(name, decoded) {
  const skip = name === "invert" || name === "composite" ? new Set([143]) : new Set();
  const sampleCheck = countMismatches(decoded, skip);
  const relock = relockCheck(decoded);
  const parity = decoded.errors.filter((error) => error.type === "PARITY");
  const checks = [];

  checks.push({
    pass: sampleCheck.mismatches.length === 0,
    label: "干净段样本",
    detail: `${sampleCheck.compared} 帧逐样本一致，差异 ${sampleCheck.mismatches.length}`
  });

  if (name === "clean") {
    checks.push({
      pass: decoded.summary.jitterCount === 0 && decoded.summary.dropoutCount === 0,
      label: "故障注入基线",
      detail: "无抖动、反转位或断流"
    });
  } else if (name === "jitter") {
    const event = insideEvent(decoded.jitter, jitter);
    checks.push({
      pass: Boolean(event),
      label: "抖动定位",
      detail: event
        ? `${event.id || "E001"} 窗口 ${(event.startNs / 1e6).toFixed(3)}–${(event.endNs / 1e6).toFixed(3)} ms，峰值 ${event.peak.toFixed(3)} UI`
        : "未检出抖动窗口"
    });
  }

  if (name === "invert" || name === "composite") {
    const hit = parity.find((error) => error.frame === 143 && error.channel === "L");
    checks.push({
      pass: Boolean(hit) && parity.length === 1,
      label: "反转位定位",
      detail: hit ? `${hit.id} 命中帧 143 / L，奇偶校验失败` : "未命中帧 143 / L"
    });
  } else {
    checks.push({
      pass: parity.length === 0,
      label: "奇偶校验",
      detail: parity.length ? `${parity.length} 个子帧失败` : "无奇偶校验错误"
    });
  }

  if (name === "dropout" || name === "composite") {
    const event = insideEvent(decoded.dropouts, dropout, 0.00005);
    checks.push({
      pass: Boolean(event),
      label: "断流定位",
      detail: event
        ? `失锁覆盖 ${(event.startNs / 1e6).toFixed(3)}–${(event.endNs / 1e6).toFixed(3)} ms`
        : "未检出断流区间"
    });
  } else {
    checks.push({
      pass: decoded.summary.lockPercent === 100,
      label: "锁定状态",
      detail: decoded.summary.lockPercent === 100
        ? "全程锁定"
        : `锁定率 ${decoded.summary.lockPercent.toFixed(2)}%`
    });
  }

  checks.push({
    pass: relock.swapped === 0 && relock.compared > 0,
    label: "重锁后声道",
    detail: `${relock.compared} 帧逐一核对，L/R 错位 ${relock.swapped}`
  });

  return {
    passed: checks.every((check) => check.pass),
    compared: sampleCheck.compared,
    mismatches: sampleCheck.mismatches.length,
    relockCompared: relock.compared,
    relockMismatches: relock.swapped,
    checks
  };
}

async function main() {
  await mkdir(OUTPUT, { recursive: true });
  const manifest = {
    description: "AES3/BMC diagnostic fixtures: known L/R tones with separately injected timing faults",
    sampleRate: SAMPLE_RATE,
    frames: FRAMES,
    tones: TONES,
    channelLabels: { A: "L", B: "R" },
    scenarios: {}
  };

  for (const [name, definition] of Object.entries(definitions)) {
    const stream = encodeAes3Stream({
      samples,
      sampleRate: SAMPLE_RATE,
      leftStatus,
      rightStatus,
      faults: definition.faults
    });
    const decoded = decodeEdges(stream.edges);
    const validation = validate(name, decoded);
    const file = `${name}.csv`;
    await writeFile(new URL(file, OUTPUT), edgeSequenceToCsv(stream.edges));
    await writeFile(new URL(`${name}-errors.csv`, OUTPUT), errorsToCsv(decoded.errors));
    await writeFile(
      new URL(`${name}-decoded.wav`, OUTPUT),
      encodeWav24(decoded.frames, SAMPLE_RATE)
    );
    manifest.scenarios[name] = {
      title: definition.title,
      file,
      edgeCount: decoded.summary.edgeCount,
      faults: definition.faults,
      summary: decoded.summary,
      validation
    };
  }

  await writeFile(new URL("manifest.json", OUTPUT), `${JSON.stringify(manifest, null, 2)}\n`);
  const passed = Object.values(manifest.scenarios).filter((scenario) => scenario.validation.passed).length;
  console.log(`Generated ${Object.keys(manifest.scenarios).length} scenarios; ${passed} validation suites passed.`);
  for (const [name, scenario] of Object.entries(manifest.scenarios)) {
    console.log(`${name.padEnd(10)} edges=${scenario.edgeCount} checks=${scenario.validation.checks.length} pass=${scenario.validation.passed}`);
  }
  if (passed !== Object.keys(manifest.scenarios).length) process.exitCode = 1;
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
