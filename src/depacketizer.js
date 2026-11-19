// RFC 6184 H.264 RTP depacketization with loss/timestamp-jump diagnostics.
import { parseRtpPacket, seqDistance } from './rtp.js';
import { isVclNal, nalType, nalTypeName, parseSliceHeader } from './nal.js';

const STAP_A_TYPE = 24;
const FU_A_TYPE = 28;
const FU_START = 0x80;
const FU_END = 0x40;

export class H264RtpDepacketizer {
  constructor({ timestampStep = 3000, maxJumpSteps = 1 } = {}) {
    this.timestampStep = timestampStep; // expected RTP timestamp increment per frame
    this.maxJumpSteps = maxJumpSteps;
    this.lastSeq = null;
    this.lastTimestamp = null;
    this.pendingFu = null; // in-progress FU-A reassembly
    this.frame = null; // NALs belonging to the access unit currently being assembled
    this.nalCounter = 0;
    this.stats = {
      packetsReceived: 0,
      gaps: 0,
      missingPackets: 0,
      timestampJumps: 0,
      nalsReassembled: 0,
      incompleteFuDropped: 0,
      orphanFragments: 0,
      framesEmitted: 0,
      reassemblyNsTotal: 0n,
    };
    this.warnings = [];
    this.onFrame = null;
    this.onWarning = null;
  }

  push(rtpBytes, now = performance.now()) {
    const rtp = parseRtpPacket(rtpBytes);
    this.stats.packetsReceived += 1;
    this.detectTransportLoss(rtp);
    this.detectTimestampJump(rtp);

    const type = rtp.payload[0] & 0x1f;
    if (type === FU_A_TYPE) {
      this.handleFuA(rtp, now);
    } else if (type === STAP_A_TYPE) {
      this.handleStapA(rtp, now);
    } else {
      this.emitNal(rtp.payload, rtp, 'single', now);
    }
    if (rtp.marker) this.flushFrame(rtp.timestamp);
  }

  warn(kind, message, detail = {}) {
    const event = { kind, message, ...detail, at: performance.now() };
    this.warnings.push(event);
    if (this.onWarning) this.onWarning(event);
  }

  detectTransportLoss(rtp) {
    if (this.lastSeq !== null) {
      const gap = seqDistance(this.lastSeq, rtp.sequenceNumber) - 1;
      if (gap > 0) {
        this.stats.gaps += 1;
        this.stats.missingPackets += gap;
        this.warn('packet_gap', `RTP sequence gap: ${gap} packet(s) lost before seq=${rtp.sequenceNumber}`, {
          missing: gap,
          afterSeq: this.lastSeq,
          seq: rtp.sequenceNumber,
          timestamp: rtp.timestamp,
        });
        if (this.pendingFu) {
          this.abortPendingFu(rtp, 'sequence gap (one or more FU-A fragments lost)');
        }
      }
    }
    this.lastSeq = rtp.sequenceNumber;
  }

  detectTimestampJump(rtp) {
    if (this.lastTimestamp === null || rtp.timestamp === this.lastTimestamp) {
      this.lastTimestamp = rtp.timestamp;
      return;
    }
    const delta = (rtp.timestamp - this.lastTimestamp) >>> 0;
    const steps = Math.round(delta / this.timestampStep);
    this.lastTimestamp = rtp.timestamp;
    if (steps >= this.maxJumpSteps + 1) {
      this.stats.timestampJumps += 1;
      this.warn('timestamp_jump', `RTP timestamp jump: delta=${delta} (~${steps} frame intervals), loss suspected`, {
        delta,
        expectedStep: this.timestampStep,
        estimatedMissingFrames: steps - 1,
        seq: rtp.sequenceNumber,
        timestamp: rtp.timestamp,
      });
    }
  }

  abortPendingFu(rtp, reason) {
    if (!this.pendingFu) return;
    const previous = this.pendingFu;
    this.stats.incompleteFuDropped += 1;
    this.warn('incomplete_fu', `Dropping incomplete FU-A NAL (type=${previous.type}): ${reason}`, {
      reason,
      timestamp: rtp.timestamp,
      fragmentsGot: previous.chunks ? previous.chunks.length : 0,
    });
    this.pendingFu = { tombstone: true, type: previous.type, timestamp: previous.timestamp };
  }

  handleStapA(rtp, now) {
    const data = rtp.payload;
    let offset = 1;
    while (offset + 2 <= data.length) {
      const size = (data[offset] << 8) | data[offset + 1];
      offset += 2;
      if (size === 0 || offset + size > data.length) {
        this.warn('malformed_stap', 'Truncated STAP-A unit; stopping parse', { seq: rtp.sequenceNumber });
        break;
      }
      this.emitNal(data.subarray(offset, offset + size), rtp, 'stap-a', now);
      offset += size;
    }
  }

  handleFuA(rtp, now) {
    const data = rtp.payload;
    const fuIndicator = data[0];
    const fuHeader = data[1];
    const start = (fuHeader & FU_START) !== 0;
    const end = (fuHeader & FU_END) !== 0;
    const originalType = fuHeader & 0x1f;
    const fragment = data.subarray(2);

    if (start) {
      if (this.pendingFu) {
        this.warn('incomplete_fu', 'New FU-A START while another NAL was incomplete; replacing it', {
          timestamp: rtp.timestamp,
        });
        this.stats.incompleteFuDropped += 1;
      }
      const reconstructedHeader = (fuIndicator & 0xe0) | originalType;
      this.pendingFu = {
        type: originalType,
        timestamp: rtp.timestamp,
        startedAt: now,
        chunks: [Uint8Array.of(reconstructedHeader), Uint8Array.from(fragment)],
      };
      return;
    }

    if (!this.pendingFu) {
      this.warn('fu_without_start', 'FU-A fragment without START received; discarding fragment', {
        seq: rtp.sequenceNumber,
        timestamp: rtp.timestamp,
      });
      this.stats.orphanFragments += 1;
      return;
    }
    if (this.pendingFu.tombstone) {
      this.warn('incomplete_fu', 'Skipping fragment belonging to NAL whose FU-A reassembly was aborted by loss', {
        seq: rtp.sequenceNumber,
        timestamp: rtp.timestamp,
      });
      if (end) this.pendingFu = null;
      return;
    }
    if (this.pendingFu.type !== originalType || this.pendingFu.timestamp !== rtp.timestamp) {
      this.abortPendingFu(rtp, 'FU-A fragment mismatched NAL type/timestamp');
      return;
    }
    this.pendingFu.chunks.push(Uint8Array.from(fragment));

    if (end) {
      const assembled = concatBytes(this.pendingFu.chunks);
      const elapsed = now - this.pendingFu.startedAt;
      this.pendingFu = null;
      this.emitNal(assembled, rtp, 'fu-a', now, elapsed);
    }
  }

  emitNal(nal, rtp, source, now, reassemblyMsOverride) {
    if (this.frame && this.frame.timestamp !== rtp.timestamp) {
      this.flushFrame(this.frame.timestamp);
    }
    if (!this.frame) this.frame = { timestamp: rtp.timestamp, nals: [] };

    const type = nalType(nal);
    const vcl = isVclNal(type);
    const slice = vcl ? parseSliceHeader(nal) : null;
    const start = now;
    const copy = Uint8Array.from(nal); // isolate from reused packet buffers
    const reassemblyMs = reassemblyMsOverride ?? (performance.now() - start);
    this.stats.reassemblyNsTotal += BigInt(Math.round(reassemblyMs * 1e6));

    this.nalCounter += 1;
    this.stats.nalsReassembled += 1;
    const record = {
      nalIndex: this.nalCounter,
      source, // 'single' | 'stap-a' | 'fu-a'
      nalType: type,
      nalTypeName: nalTypeName(type),
      vcl,
      sliceIndex: slice ? slice.sliceIndex : null,
      sliceType: slice ? slice.sliceTypeName : null,
      size: copy.length,
      timestamp: rtp.timestamp,
      reassemblyMs,
      data: copy,
    };
    this.frame.nals.push(record);
  }

  flushFrame(timestamp) {
    if (!this.frame || this.frame.nals.length === 0) {
      this.frame = null;
      return;
    }
    const frame = this.frame;
    this.frame = null;
    this.stats.framesEmitted += 1;
    const output = {
      timestamp,
      nals: frame.nals,
      isKeyframe: frame.nals.some((n) => n.nalType === 5),
      annexb: buildAnnexB(frame.nals.map((n) => n.data)),
    };
    if (this.onFrame) this.onFrame(output);
    return output;
  }
}

function concatBytes(chunks) {
  const total = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const out = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    out.set(chunk, offset);
    offset += chunk.length;
  }
  return out;
}

export function buildAnnexB(nals) {
  const total = nals.reduce((sum, nal) => sum + 4 + nal.length, 0);
  const out = new Uint8Array(total);
  let offset = 0;
  for (const nal of nals) {
    out.set([0, 0, 0, 1], offset);
    offset += 4;
    out.set(nal, offset);
    offset += nal.length;
  }
  return out;
}
