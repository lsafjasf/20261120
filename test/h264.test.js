import test from 'node:test';
import assert from 'node:assert/strict';
import { H264RtpPacker } from '../src/packer.js';
import { H264RtpDepacketizer, buildAnnexB } from '../src/depacketizer.js';
import { parseRtpPacket } from '../src/rtp.js';
import { splitAnnexB } from '../src/nal.js';
import {
  makeAud,
  makeIdrSlice,
  makePps,
  makePSlice0,
  makePSlice1,
  makeSps,
  bytesEqual,
} from '../src/synthetic-nal.js';

function collect(depacketizer) {
  const frames = [];
  const warnings = [];
  depacketizer.onFrame = (frame) => frames.push(frame);
  depacketizer.onWarning = (event) => warnings.push(event);
  return { frames, warnings };
}

test('single NAL mode: round-trips small NAL and reports slice index', () => {
  const packer = new H264RtpPacker({ maxPayload: 1400 });
  const depack = new H264RtpDepacketizer();
  const { frames } = collect(depack);

  const slice = makePSlice0(100);
  const packets = packer.packFrame([slice], 3000);
  assert.equal(packets.length, 1);
  const rtp = parseRtpPacket(packets[0]);
  assert.equal(rtp.payload[0] & 0x1f, 1); // plain single NAL, type 1
  assert.equal(rtp.marker, true);

  for (const packet of packets) depack.push(packet);
  assert.equal(frames.length, 1);
  assert.equal(frames[0].nals[0].source, 'single');
  assert.equal(frames[0].nals[0].sliceIndex, 0);
  assert.equal(frames[0].nals[0].sliceType, 'P');
  assert.equal(typeof frames[0].nals[0].reassemblyMs, 'number');
  assert.ok(bytesEqual(frames[0].nals[0].data, slice));
});

test('STAP-A: SPS+PPS aggregated into one packet and unpacked separately', () => {
  const packer = new H264RtpPacker({ maxPayload: 1400 });
  const depack = new H264RtpDepacketizer();
  const { frames } = collect(depack);

  const sps = makeSps();
  const pps = makePps();
  const idr = makeIdrSlice(300);
  const packets = packer.packFrame([sps, pps, idr], 0);

  assert.equal(packets.length, 2); // STAP-A + IDR single NAL
  const stapRtp = parseRtpPacket(packets[0]);
  assert.equal(stapRtp.payload[0] & 0x1f, 24);
  assert.equal(stapRtp.marker, false);
  assert.equal(packer.stats.stapA, 1);

  for (const packet of packets) depack.push(packet);
  assert.equal(frames.length, 1);
  const sources = frames[0].nals.map((nal) => nal.source);
  assert.deepEqual(sources, ['stap-a', 'stap-a', 'single']);
  assert.ok(bytesEqual(frames[0].nals[0].data, sps));
  assert.ok(bytesEqual(frames[0].nals[1].data, pps));
  assert.equal(frames[0].isKeyframe, true);
});

test('FU-A: oversized NAL is fragmented, reassembled byte-identically with slice index', () => {
  const packer = new H264RtpPacker({ maxPayload: 100 });
  const depack = new H264RtpDepacketizer();
  const { frames } = collect(depack);

  const bigSlice = makePSlice1(450); // needs multiple FU-A fragments
  const packets = packer.packFrame([bigSlice], 6000);
  assert.ok(packets.length > 2);
  assert.equal(packer.stats.fuA, 1);

  const first = parseRtpPacket(packets[0]);
  const last = parseRtpPacket(packets[packets.length - 1]);
  assert.equal(first.payload[1] & 0x80, 0x80); // START bit
  assert.equal(first.payload[1] & 0x40, 0);
  assert.equal(last.payload[1] & 0x40, 0x40); // END bit
  assert.equal(last.marker, true);

  for (const packet of packets) depack.push(packet);
  assert.equal(frames.length, 1);
  const nal = frames[0].nals[0];
  assert.equal(nal.source, 'fu-a');
  assert.equal(nal.sliceIndex, 1); // first_mb_in_slice=1
  assert.ok(bytesEqual(nal.data, bigSlice));
  assert.ok(nal.reassemblyMs >= 0);
});

test('Annex-B output can be re-split into the original NAL units', () => {
  const packer = new H264RtpPacker({ maxPayload: 80 });
  const depack = new H264RtpDepacketizer();
  const { frames } = collect(depack);

  const nals = [makeAud(), makeSps(), makePps(), makeIdrSlice(300)];
  for (const packet of packer.packFrame(nals, 9000)) depack.push(packet);
  const rebuilt = splitAnnexB(frames[0].annexb);
  assert.equal(rebuilt.length, nals.length);
  nals.forEach((nal, i) => assert.ok(bytesEqual(rebuilt[i], nal)));
});
