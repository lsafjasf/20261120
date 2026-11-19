// Minimal RFC 3550 RTP packet reader/writer (no CSRC/extension support needed here).

export const RTP_VERSION = 2;
export const FIXED_HEADER_LENGTH = 12;

export function buildRtpPacket({
  payloadType,
  marker = false,
  sequenceNumber,
  timestamp,
  ssrc,
  payload,
}) {
  if (!Number.isInteger(sequenceNumber) || sequenceNumber < 0 || sequenceNumber > 0xffff) {
    throw new RangeError(`invalid RTP sequence number: ${sequenceNumber}`);
  }
  const packet = new Uint8Array(FIXED_HEADER_LENGTH + payload.length);
  const view = new DataView(packet.buffer);
  packet[0] = (RTP_VERSION << 6); // V=2, P=0, X=0, CC=0
  packet[1] = (marker ? 0x80 : 0) | (payloadType & 0x7f);
  view.setUint16(2, sequenceNumber);
  view.setUint32(4, timestamp >>> 0);
  view.setUint32(8, ssrc >>> 0);
  packet.set(payload, FIXED_HEADER_LENGTH);
  return packet;
}

export function parseRtpPacket(bytes) {
  if (bytes.length < FIXED_HEADER_LENGTH) {
    throw new RangeError('packet shorter than fixed RTP header');
  }
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const version = bytes[0] >>> 6;
  if (version !== RTP_VERSION) {
    throw new RangeError(`unsupported RTP version: ${version}`);
  }
  const csrcCount = bytes[0] & 0x0f;
  const headerLength = FIXED_HEADER_LENGTH + csrcCount * 4;
  return {
    version,
    padding: (bytes[0] & 0x20) !== 0,
    extension: (bytes[0] & 0x10) !== 0,
    csrcCount,
    marker: (bytes[1] & 0x80) !== 0,
    payloadType: bytes[1] & 0x7f,
    sequenceNumber: view.getUint16(2),
    timestamp: view.getUint32(4),
    ssrc: view.getUint32(8),
    payload: bytes.subarray(headerLength),
  };
}

// 16-bit wraparound-aware forward distance: how many steps from a to b.
export function seqDistance(a, b) {
  return (b - a + 0x10000) & 0xffff;
}

export function nextSeq(seq) {
  return (seq + 1) & 0xffff;
}
