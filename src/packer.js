// RFC 6184 H.264 RTP packetization: single NAL, STAP-A aggregation and FU-A fragmentation.
import { buildRtpPacket } from './rtp.js';
import { isVclNal } from './nal.js';

const STAP_A_TYPE = 24;
const FU_A_TYPE = 28;

export class H264RtpPacker {
  constructor({ payloadType = 96, ssrc = 0x12345678, maxPayload = 1400, startSeq = 0 } = {}) {
    if (maxPayload < 2) throw new RangeError('maxPayload must be >= 2');
    this.payloadType = payloadType;
    this.ssrc = ssrc;
    this.maxPayload = maxPayload; // max RTP payload bytes
    this.seq = startSeq & 0xffff;
    this.stats = { singleNal: 0, stapA: 0, fuA: 0, packets: 0 };
  }

  emit(payload, marker) {
    const packet = buildRtpPacket({
      payloadType: this.payloadType,
      marker,
      sequenceNumber: this.seq,
      timestamp: this.timestamp,
      ssrc: this.ssrc,
      payload,
    });
    this.seq = (this.seq + 1) & 0xffff;
    this.stats.packets += 1;
    return packet;
  }

  // Pack one access unit (array of NAL Uint8Arrays) into RTP packets.
  packFrame(nals, timestamp) {
    this.timestamp = timestamp >>> 0;
    const packets = [];
    let index = 0;

    while (index < nals.length) {
      const nal = nals[index];
      if (nal.length <= this.maxPayload) {
        // Greedily aggregate following small non-VCL NALs into STAP-A while the payload fits.
        const aggregated = [nal];
        let stapSize = 1 + 2 + nal.length;
        let next = index + 1;
        while (next < nals.length && !isVclNal(nal[0] & 0x1f)) {
          const candidate = nals[next];
          const candidateSize = 2 + candidate.length;
          if (isVclNal(candidate[0] & 0x1f)
            || candidate.length > this.maxPayload
            || stapSize + candidateSize > this.maxPayload) {
            break;
          }
          aggregated.push(candidate);
          stapSize += candidateSize;
          next += 1;
        }

        if (aggregated.length > 1) {
          const payload = new Uint8Array(stapSize);
          payload[0] = (nal[0] & 0xe0) | STAP_A_TYPE;
          let offset = 1;
          for (const unit of aggregated) {
            new DataView(payload.buffer).setUint16(offset, unit.length);
            offset += 2;
            payload.set(unit, offset);
            offset += unit.length;
          }
          const last = next === nals.length;
          packets.push(this.emit(payload, last));
          this.stats.stapA += 1;
          index = next;
        } else {
          const last = index + 1 === nals.length;
          packets.push(this.emit(nal, last));
          this.stats.singleNal += 1;
          index += 1;
        }
      } else {
        packets.push(...this.fragmentFuA(nal, index === nals.length - 1));
        this.stats.fuA += 1;
        index += 1;
      }
    }
    return packets;
  }

  fragmentFuA(nal, lastNalInFrame) {
    const indicator = (nal[0] & 0xe0) | FU_A_TYPE;
    const originalHeader = nal[0] & 0x1f;
    const body = nal.subarray(1);
    const chunkSize = this.maxPayload - 2; // FU indicator + FU header
    const packets = [];
    let offset = 0;
    let first = true;
    while (offset < body.length) {
      const end = Math.min(offset + chunkSize, body.length);
      const isLastFragment = end === body.length;
      const fuHeader = originalHeader
        | (first ? 0x80 : 0)
        | (isLastFragment ? 0x40 : 0);
      const payload = new Uint8Array(2 + (end - offset));
      payload[0] = indicator;
      payload[1] = fuHeader;
      payload.set(body.subarray(offset, end), 2);
      packets.push(this.emit(payload, lastNalInFrame && isLastFragment));
      first = false;
      offset = end;
    }
    return packets;
  }
}
