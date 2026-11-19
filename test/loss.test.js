import test from 'node:test';
import assert from 'node:assert/strict';
import { H264RtpPacker } from '../src/packer.js';
import { H264RtpDepacketizer } from '../src/depacketizer.js';
import { makeIdrSlice, makePSlice0, makePSlice1 } from '../src/synthetic-nal.js';

function sink(depacketizer) {
  const frames = [];
  const warnings = [];
  depacketizer.onFrame = (frame) => frames.push(frame);
  depacketizer.onWarning = (event) => warnings.push(event);
  return { frames, warnings };
}

test('timestamp jump between frames raises a warning', () => {
  const packer = new H264RtpPacker();
  const depack = new H264RtpDepacketizer({ timestampStep: 3000 });
  const { frames, warnings } = sink(depack);

  const f0 = packer.packFrame([makePSlice0(40)], 0);
  const f1 = packer.packFrame([makePSlice0(40)], 3000);
  const f2 = packer.packFrame([makePSlice0(40)], 12000); // +9000 => skips 2 frames
  const f3 = packer.packFrame([makePSlice0(40)], 15000);

  [...f0, ...f1, ...f2, ...f3].forEach((packet) => depack.push(packet));
  assert.equal(frames.length, 4);
  const jumpWarnings = warnings.filter((w) => w.kind === 'timestamp_jump');
  assert.equal(jumpWarnings.length, 1);
  assert.equal(jumpWarnings[0].delta, 9000);
  assert.equal(jumpWarnings[0].estimatedMissingFrames, 2);
  assert.equal(depack.stats.timestampJumps, 1);
});

test('lost FU-A middle fragment: incomplete NAL skipped, later frames keep decoding', () => {
  const maxPayload = 60;
  const packer = new H264RtpPacker({ maxPayload, startSeq: 100 });
  const depack = new H264RtpDepacketizer();
  const { frames, warnings } = sink(depack);

  // Frame A: oversized NAL split into FU-A packets; drop one middle fragment.
  const frameA = packer.packFrame([makePSlice1(300)], 3000);
  assert.ok(frameA.length >= 4, 'fixture must produce >=4 FU-A packets');
  const dropIndex = 2;
  const droppedSeq = (100 + dropIndex) & 0xffff;
  const frameAWithLoss = frameA.filter((_, i) => i !== dropIndex);

  // Following frames arrive intact (sequence numbers keep increasing from packer).
  const frameB = packer.packFrame([makePSlice0(60)], 6000);
  const frameC = packer.packFrame([makeIdrSlice(60)], 9000);

  [...frameAWithLoss, ...frameB, ...frameC].forEach((packet) => depack.push(packet));

  // Frame A produced nothing; B and C are still emitted.
  assert.equal(frames.length, 2);
  assert.equal(frames[0].timestamp, 6000);
  assert.equal(frames[1].timestamp, 9000);
  assert.equal(frames[1].isKeyframe, true);

  const gap = warnings.find((w) => w.kind === 'packet_gap');
  assert.ok(gap);
  assert.equal(gap.seq, (droppedSeq + 1) & 0xffff);
  const dropped = warnings.find((w) => w.kind === 'incomplete_fu');
  assert.ok(dropped);
  assert.equal(depack.stats.incompleteFuDropped, 1);
  assert.equal(depack.stats.missingPackets, 1);
});

test('lost FU-A END fragment is also detected and discarded', () => {
  const packer = new H264RtpPacker({ maxPayload: 50 });
  const depack = new H264RtpDepacketizer();
  const { frames, warnings } = sink(depack);

  const frameA = packer.packFrame([makePSlice0(260)], 0);
  const withoutEnd = frameA.slice(0, -1);
  const frameB = packer.packFrame([makePSlice0(30)], 3000);

  [...withoutEnd, ...frameB].forEach((packet) => depack.push(packet));
  assert.equal(frames.length, 1);
  assert.equal(frames[0].timestamp, 3000);
  assert.ok(warnings.some((w) => w.kind === 'incomplete_fu'));
  assert.equal(depack.stats.incompleteFuDropped, 1);
});

test('reassembly timing stats accumulate per emitted NAL', () => {
  const packer = new H264RtpPacker({ maxPayload: 80 });
  const depack = new H264RtpDepacketizer();
  sink(depack);

  const packets = [
    ...packer.packFrame([makeSpsStub(), makePpsStub(), makeIdrSlice(250)], 0),
    ...packer.packFrame([makePSlice0(250)], 3000),
  ];
  packets.forEach((packet) => depack.push(packet));
  assert.equal(depack.stats.nalsReassembled, 4);
  assert.equal(depack.stats.framesEmitted, 2);
  assert.ok(depack.stats.reassemblyNsTotal >= 0n);
});

function makeSpsStub() {
  const nal = new Uint8Array(12);
  nal[0] = 0x67;
  return nal;
}
function makePpsStub() {
  const nal = new Uint8Array(8);
  nal[0] = 0x68;
  return nal;
}
