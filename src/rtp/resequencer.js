'use strict';

const { RtpJitterEstimator } = require('./jitter');
const { summarize } = require('../utils');

const REORDER_BINS = [
  { label: '0 <= x < 5 ms', min: 0, max: 5 },
  { label: '5 <= x < 10 ms', min: 5, max: 10 },
  { label: '10 <= x < 20 ms', min: 10, max: 20 },
  { label: '20 <= x < 30 ms', min: 20, max: 30 },
  { label: 'x >= 30 ms', min: 30, max: Infinity },
];

const JITTER_BINS = [
  { label: '0 <= x < 5 ms', min: 0, max: 5 },
  { label: '5 <= x < 15 ms', min: 5, max: 15 },
  { label: '15 <= x < 30 ms', min: 15, max: 30 },
  { label: '30 <= x < 50 ms', min: 30, max: 50 },
  { label: 'x >= 50 ms', min: 50, max: Infinity },
];

class RtpResequencer {
  constructor({ name, clockRate, sequenceNumber, timestamp, generation = 0, meta = {} }) {
    this.name = name;
    this.clockRate = clockRate;
    this.baseSequence = sequenceNumber;
    this.expectedSequence = sequenceNumber;
    this.buffer = new Map();
    this.history = [];
    this.generation = generation;
    this.meta = { ...meta, sequenceNumber, timestamp, generation };
    this.generationMeta = new Map([[generation, this.meta]]);
    this.jitter = new RtpJitterEstimator(clockRate);
    this.duplicateCount = 0;
    this.lateCount = 0;
    this.droppedOnRebase = 0;
  }

  unwrapSequence(sequenceNumber) {
    const difference = ((sequenceNumber - this.baseSequence + 32768) & 0xffff) - 32768;
    return this.baseSequence + difference;
  }

  push(packet, receivedAt) {
    const jitterObservation = this.jitter.observe(receivedAt, packet.timestamp);
    const extendedSequence = this.unwrapSequence(packet.sequenceNumber);
    const entry = {
      generation: this.generation,
      sequenceNumber: packet.sequenceNumber,
      extendedSequence,
      timestamp: packet.timestamp,
      marker: packet.marker,
      payloadType: packet.payloadType,
      ssrc: packet.ssrc,
      receivedAt,
      releasedAt: null,
      reorderDelayMs: null,
      transitMs: jitterObservation.transitMs,
      jitterDeltaMs: jitterObservation.deltaMs,
      smoothedJitterMs: jitterObservation.jitterMs,
    };

    if (extendedSequence < this.expectedSequence) {
      this.lateCount += 1;
      return [];
    }
    if (this.buffer.has(extendedSequence)) {
      this.duplicateCount += 1;
      return [];
    }

    this.buffer.set(extendedSequence, entry);
    const released = [];
    while (this.buffer.has(this.expectedSequence)) {
      const ready = this.buffer.get(this.expectedSequence);
      this.buffer.delete(this.expectedSequence);
      ready.releasedAt = receivedAt;
      ready.reorderDelayMs = Math.max(0, receivedAt - ready.receivedAt);
      this.history.push(ready);
      released.push(ready);
      this.expectedSequence += 1;
    }
    return released;
  }

  rebase({ sequenceNumber, timestamp, generation, ...meta }) {
    this.droppedOnRebase += this.buffer.size;
    this.buffer.clear();
    this.generation = generation;
    this.baseSequence = sequenceNumber;
    this.expectedSequence = sequenceNumber;
    this.jitter = new RtpJitterEstimator(this.clockRate);
    this.meta = { ...meta, sequenceNumber, timestamp, generation };
    this.generationMeta.set(generation, this.meta);
  }

  packetsFor(generation = this.generation) {
    return this.history.filter((entry) => entry.generation === generation);
  }

  snapshot(generation = this.generation) {
    const packets = this.packetsFor(generation);
    const reorderDelays = packets
      .map((entry) => entry.reorderDelayMs)
      .filter((value) => value !== null);
    const reorderedDelays = reorderDelays.filter((value) => value > 0.1);
    const jitterDeltas = packets
      .map((entry) => entry.jitterDeltaMs)
      .filter((value) => value !== null)
      .map(Math.abs);
    const smoothed = packets
      .map((entry) => entry.smoothedJitterMs)
      .filter((value) => value !== null);
    const first = packets[0] || null;
    const last = packets[packets.length - 1] || null;
    const contiguous = packets.every((entry, index) => {
      if (index === 0) return true;
      return entry.extendedSequence === packets[index - 1].extendedSequence + 1;
    });

    return {
      name: this.name,
      clockRate: this.clockRate,
      generation,
      meta: this.generationMeta.get(generation) || this.meta,
      packetCount: packets.length,
      duplicateCount: this.duplicateCount,
      lateCount: this.lateCount,
      droppedOnRebase: this.droppedOnRebase,
      contiguous,
      first: first
        ? { sequenceNumber: first.sequenceNumber, timestamp: first.timestamp }
        : null,
      last: last
        ? { sequenceNumber: last.sequenceNumber, timestamp: last.timestamp }
        : null,
      reorder: {
        allPackets: summarize(reorderDelays, { bins: REORDER_BINS }),
        reorderedPackets: summarize(reorderedDelays, { bins: REORDER_BINS }),
      },
      jitter: {
        transitDeltaAbs: summarize(jitterDeltas, { bins: JITTER_BINS }),
        finalRfc3550JitterMs: smoothed.length > 0 ? smoothed[smoothed.length - 1] : 0,
      },
      packets: packets.map((entry) => ({
        sequenceNumber: entry.sequenceNumber,
        timestamp: entry.timestamp,
        reorderDelayMs: Number(entry.reorderDelayMs.toFixed(3)),
        transitDeltaMs:
          entry.jitterDeltaMs === null ? null : Number(entry.jitterDeltaMs.toFixed(3)),
        smoothedJitterMs: Number(entry.smoothedJitterMs.toFixed(3)),
      })),
    };
  }
}

module.exports = { JITTER_BINS, REORDER_BINS, RtpResequencer };
