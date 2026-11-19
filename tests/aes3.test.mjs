import test from "node:test";
import assert from "node:assert/strict";
import {
  decodeEdges,
  encodeAes3Stream,
  encodeSubframe,
  parseEdgeCsv,
  edgeSequenceToCsv
} from "../src/aes3.js";
import { encodeWav24, errorsToCsv } from "../src/export.js";

const SAMPLE_RATE = 8000;
const FRAMES = 256;
const samples = Array.from({ length: FRAMES }, (_, frame) => ({
  left: Math.round(7_000_000 * Math.sin((2 * Math.PI * 1000 * frame) / SAMPLE_RATE)) || 0,
  right: Math.round(7_000_000 * Math.cos((2 * Math.PI * 1500 * frame) / SAMPLE_RATE)) || 0
}));

const statusA = new Uint8Array(24);
const statusB = new Uint8Array(24);
statusA[0] = statusB[0] = 1;
statusA[20] = "L".charCodeAt(0);
statusB[20] = "R".charCodeAt(0);

const jitter = { start: 0.00768, end: 0.01216, amplitudeUI: 0.18 };
const bitFlip = { frame: 143, channel: "A", slot: 16 };
const dropout = { start: 0.02176, end: 0.02432 };

function makeStream(faults = {}) {
  return encodeAes3Stream({
    samples,
    sampleRate: SAMPLE_RATE,
    leftStatus: statusA,
    rightStatus: statusB,
    faults
  });
}

function presentMismatches(decoded, skip = new Set()) {
  const mismatches = [];
  let compared = 0;
  for (const frame of decoded.frames) {
    if (!frame.leftPresent || !frame.rightPresent || skip.has(frame.frameIndex)) continue;
    compared += 1;
    if (frame.left !== samples[frame.frameIndex].left || frame.right !== samples[frame.frameIndex].right) {
      mismatches.push(frame.frameIndex);
    }
  }
  return { compared, mismatches };
}

test("AES3 preamble tables and LSB-first parity framing", () => {
  const z = encodeSubframe({ sample: 0x412abd, preamble: "Z", priorState: 0 });
  const x = encodeSubframe({ sample: 0x412abd, preamble: "X", priorState: 0 });
  const y = encodeSubframe({ sample: 0x412abd, preamble: "Y", priorState: 1 });
  assert.equal(z.states.slice(0, 8).join(""), "11101000");
  assert.equal(x.states.slice(0, 8).join(""), "11100010");
  assert.equal(y.states.slice(0, 8).join(""), "00011011");
  assert.equal(z.bits.length, 28);
  assert.deepEqual(z.bits.slice(0, 8), [1, 0, 1, 1, 1, 1, 0, 1]);
  assert.equal(z.bits.reduce((sum, bit) => sum + bit, 0) % 2, 0);
});

test("clean stream recovers every PCM sample and both channel labels", () => {
  const decoded = decodeEdges(makeStream().edges);
  const compared = presentMismatches(decoded);
  assert.equal(decoded.summary.edgeCount, makeStream().edges.length);
  assert.equal(decoded.summary.sampleRate, SAMPLE_RATE);
  assert.equal(decoded.summary.bitClock, 512000);
  assert.equal(decoded.summary.subframeCount, 512);
  assert.equal(decoded.summary.frameCount, 256);
  assert.equal(compared.compared, 256);
  assert.deepEqual(compared.mismatches, []);
  assert.equal(decoded.summary.parityErrorCount, 0);
  assert.equal(decoded.summary.dropoutCount, 0);
  assert.equal(decoded.summary.jitterCount, 0);
  assert.equal(decoded.channelStatus.A.label, "L");
  assert.equal(decoded.channelStatus.B.label, "R");
});

test("clock jitter is localized without losing samples", () => {
  const decoded = decodeEdges(makeStream({ jitter }).edges);
  const compared = presentMismatches(decoded);
  assert.equal(compared.compared, 256);
  assert.deepEqual(compared.mismatches, []);
  assert.equal(decoded.summary.jitterCount, 1);
  assert.equal(decoded.summary.lockPercent, 100);
  const event = decoded.jitter[0];
  assert.ok(event.startNs / 1e9 <= jitter.end);
  assert.ok(event.endNs / 1e9 >= jitter.start);
  assert.ok(event.peak > 0.15 && event.peak < 0.2);
  assert.equal(decoded.errors.filter((error) => error.type === "CLOCK_JITTER").length, 1);
});

test("one inverted bit creates one parity failure at frame 143 L", () => {
  const decoded = decodeEdges(makeStream({ bitFlip }).edges);
  const compared = presentMismatches(decoded, new Set([143]));
  assert.equal(compared.compared, 255);
  assert.deepEqual(compared.mismatches, []);
  assert.equal(decoded.frames[143].left === samples[143].left, false);
  assert.equal(decoded.summary.parityErrorCount, 1);
  const errors = decoded.errors.filter((error) => error.type === "PARITY");
  assert.equal(errors.length, 1);
  assert.equal(errors[0].frame, 143);
  assert.equal(errors[0].channel, "L");
});

test("dropout is localized and relock preserves L/R assignment", () => {
  const decoded = decodeEdges(makeStream({ dropout }).edges);
  const compared = presentMismatches(decoded);
  assert.equal(compared.compared, 235);
  assert.deepEqual(compared.mismatches, []);
  assert.equal(decoded.summary.dropoutCount, 1);
  assert.ok(decoded.summary.lockPercent > 91 && decoded.summary.lockPercent < 92);
  const event = decoded.dropouts[0];
  assert.ok(Math.abs(event.startNs / 1e9 - dropout.start) < 0.00005);
  assert.ok(Math.abs(event.endNs / 1e9 - dropout.end) < 0.00005);
  const after = decoded.frames.filter((frame) => frame.frameIndex >= 200
    && frame.leftPresent && frame.rightPresent);
  assert.equal(after.length, 56);
  for (const frame of after) {
    assert.equal(frame.left, samples[frame.frameIndex].left);
    assert.equal(frame.right, samples[frame.frameIndex].right);
  }
});

test("composite scenario reports jitter, one parity fault, and dropout", () => {
  const decoded = decodeEdges(makeStream({ jitter, bitFlip, dropout }).edges);
  assert.deepEqual(
    decoded.errors.map((error) => error.type),
    ["CLOCK_JITTER", "PARITY", "DROPOUT"]
  );
  assert.equal(decoded.frames[143].left === samples[143].left, false);
  assert.ok(decoded.frames.slice(200).every((frame) => (
    !frame.leftPresent || !frame.rightPresent ||
    (frame.left === samples[frame.frameIndex].left && frame.right === samples[frame.frameIndex].right)
  )));
});

test("CSV and 24-bit stereo WAV exports retain the decoded timeline", () => {
  const stream = makeStream({ bitFlip });
  const parsed = parseEdgeCsv(edgeSequenceToCsv(stream.edges));
  assert.equal(parsed.edges.length, stream.edges.length);
  const decoded = decodeEdges(parsed.edges);
  const wav = encodeWav24(decoded.frames, SAMPLE_RATE);
  const view = new DataView(wav.buffer, wav.byteOffset, wav.byteLength);
  assert.equal(String.fromCharCode(...wav.slice(0, 4)), "RIFF");
  assert.equal(view.getUint16(22, true), 2);
  assert.equal(view.getUint32(24, true), SAMPLE_RATE);
  assert.equal(view.getUint16(34, true), 24);
  assert.equal(view.getUint32(40, true), 256 * 6);
  const csv = errorsToCsv(decoded.errors);
  assert.match(csv, /PARITY/);
  assert.match(csv, /frame,channel/);
});
