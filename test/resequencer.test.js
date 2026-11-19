'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { RtpResequencer } = require('../src/rtp/resequencer');

function packet(sequenceNumber, timestamp) {
  return {
    sequenceNumber,
    timestamp,
    marker: false,
    payloadType: 96,
    ssrc: 0x11223344,
  };
}

test('resequences out-of-order RTP packets and records buffering delay', () => {
  const resequencer = new RtpResequencer({
    name: 'video',
    clockRate: 90000,
    sequenceNumber: 1000,
    timestamp: 0,
    generation: 1,
  });

  assert.equal(resequencer.push(packet(1000, 0), 0).length, 1);
  assert.equal(resequencer.push(packet(1002, 3600), 20).length, 0);
  assert.equal(resequencer.push(packet(1001, 1800), 40).length, 2);

  const snapshot = resequencer.snapshot(1);
  assert.equal(snapshot.packetCount, 3);
  assert.equal(snapshot.contiguous, true);
  assert.deepEqual(
    snapshot.packets.map((entry) => entry.sequenceNumber),
    [1000, 1001, 1002],
  );
  assert.equal(snapshot.packets[2].reorderDelayMs, 20);
  assert.equal(snapshot.reorder.reorderedPackets.count, 1);
  assert.equal(snapshot.reorder.reorderedPackets.p50, 20);
  assert.ok(snapshot.jitter.transitDeltaAbs.max >= 40);
});

test('resequencer handles RTP sequence-number wraparound', () => {
  const resequencer = new RtpResequencer({
    name: 'audio',
    clockRate: 48000,
    sequenceNumber: 65534,
    timestamp: 0,
    generation: 1,
  });

  resequencer.push(packet(65534, 0), 0);
  resequencer.push(packet(0, 960), 20);
  resequencer.push(packet(65535, 1920), 40);
  const snapshot = resequencer.snapshot(1);

  assert.equal(snapshot.packetCount, 3);
  assert.equal(snapshot.contiguous, true);
  assert.deepEqual(
    snapshot.packets.map((entry) => entry.sequenceNumber),
    [65534, 65535, 0],
  );
});

test('rebase drops old-generation packets and starts at the seek RTP-Info', () => {
  const resequencer = new RtpResequencer({
    name: 'video',
    clockRate: 90000,
    sequenceNumber: 2000,
    timestamp: 0,
    generation: 1,
  });
  resequencer.push(packet(2000, 0), 0);
  resequencer.push(packet(2002, 3600), 20);

  resequencer.rebase({
    sequenceNumber: 2125,
    timestamp: 225000,
    generation: 2,
    nptSeconds: 2.5,
  });
  resequencer.push(packet(2125, 225000), 100);
  resequencer.push(packet(2126, 226800), 120);

  const snapshot = resequencer.snapshot(2);
  assert.equal(snapshot.droppedOnRebase, 1);
  assert.equal(snapshot.first.sequenceNumber, 2125);
  assert.equal(snapshot.first.timestamp, 225000);
  assert.equal(snapshot.contiguous, true);
});
