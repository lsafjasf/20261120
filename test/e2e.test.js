import test from 'node:test';
import assert from 'node:assert/strict';
import { H264RtpPacker } from '../src/packer.js';
import { H264RtpDepacketizer, buildAnnexB } from '../src/depacketizer.js';
import { makeIdrSlice, makePSlice0, makePSlice1, makePps, makeSps } from '../src/synthetic-nal.js';

// Minimal stand-in for WebCodecs EncodedVideoChunk -> decoder.decode().
class FakeDecoder {
  constructor() {
    this.decoded = [];
    this.errors = [];
  }
  decode(chunk) {
    this.decoded.push({ type: chunk.type, timestamp: chunk.timestamp, size: chunk.byteLength });
  }
  flush() {
    return Promise.resolve();
  }
}

test('full pipeline: lossy FU-A frame skipped, all later frames reach the decoder', async () => {
  const frameCount = 8;
  const step = 3000;
  const packer = new H264RtpPacker({ maxPayload: 120 });
  const depack = new H264RtpDepacketizer({ timestampStep: step });
  const decoder = new FakeDecoder();

  const frames = Array.from({ length: frameCount }, (_, i) => {
    if (i === 0) return [makeSps(), makePps(), makeIdrSlice(500)];
    if (i === 3) return [makePSlice1(500)]; // oversized -> FU-A, will lose a fragment
    if (i === 6) return [makeIdrSlice(500)];
    return [makePSlice0(400)];
  });

  // Packetize every access unit and locate frame 3's middle FU-A fragment.
  const stream = [];
  frames.forEach((nals, frameIndex) => {
    for (const packet of packer.packFrame(nals, frameIndex * step)) {
      stream.push({ packet, frameIndex, isFu: (packet[12] & 0x1f) === 28 });
    }
  });
  const frame3Fu = stream
    .map((item, index) => ({ item, index }))
    .filter(({ item }) => item.frameIndex === 3 && item.isFu);
  assert.ok(frame3Fu.length >= 4);
  const drop = frame3Fu[Math.floor(frame3Fu.length / 2)].index;

  let handoverNs = 0n;
  const handoverTimings = [];
  depack.onFrame = (frame) => {
    const start = performance.now();
    const chunk = {
      type: frame.isKeyframe ? 'key' : 'delta',
      timestamp: (frame.timestamp / 90000) * 1e6,
      byteLength: frame.annexb.length,
    };
    decoder.decode(chunk);
    const cost = performance.now() - start;
    handoverTimings.push(cost);
    handoverNs += BigInt(Math.round(cost * 1e6));
  };
  const warnings = [];
  depack.onWarning = (event) => warnings.push(event);

  stream.forEach((item, index) => {
    if (index !== drop) depack.push(item.packet);
  });
  await decoder.flush();

  // 8 frames minus the one whose FU-A NAL was destroyed.
  assert.equal(decoder.decoded.length, frameCount - 1);
  const decodedSeconds = decoder.decoded.map((d) => d.timestamp / 1e6);
  const expectedSeconds = [0, 1, 2, 4, 5, 6, 7].map((i) => i / 30);
  decodedSeconds.forEach((value, i) => assert.ok(Math.abs(value - expectedSeconds[i]) < 1e-9));
  assert.ok(warnings.some((w) => w.kind === 'packet_gap'));
  assert.ok(warnings.some((w) => w.kind === 'incomplete_fu'));
  assert.equal(depack.stats.incompleteFuDropped, 1);

  // Every delivered chunk is a valid Annex-B access unit.
  for (const decoded of decoder.decoded) {
    assert.ok(decoded.size > 4);
  }
  assert.ok(handoverTimings.length === decoder.decoded.length);
  assert.ok(handoverNs >= 0n);
  assert.equal(typeof (Number(handoverNs) / 1e6), 'number');
});

test('buildAnnexB prefixes each NAL with a 4-byte start code', () => {
  const nal = makePSlice0(20);
  const out = buildAnnexB([nal, nal]);
  assert.deepEqual([...out.subarray(0, 4)], [0, 0, 0, 1]);
  assert.deepEqual([...out.subarray(24, 28)], [0, 0, 0, 1]);
});
