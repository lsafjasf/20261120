'use strict';

function encodeRtpPacket({
  sequenceNumber,
  timestamp,
  ssrc,
  payloadType,
  marker = false,
  payload = Buffer.alloc(0),
}) {
  const packet = Buffer.alloc(12 + payload.length);
  packet[0] = 0x80;
  packet[1] = (marker ? 0x80 : 0) | (payloadType & 0x7f);
  packet.writeUInt16BE(sequenceNumber & 0xffff, 2);
  packet.writeUInt32BE(timestamp >>> 0, 4);
  packet.writeUInt32BE(ssrc >>> 0, 8);
  payload.copy(packet, 12);
  return packet;
}

function parseRtpPacket(buffer) {
  if (!Buffer.isBuffer(buffer) || buffer.length < 12) {
    throw new Error('RTP packet is shorter than the 12-byte fixed header');
  }
  const version = buffer[0] >>> 6;
  if (version !== 2) throw new Error(`Unsupported RTP version ${version}`);

  const hasPadding = Boolean(buffer[0] & 0x20);
  const hasExtension = Boolean(buffer[0] & 0x10);
  const csrcCount = buffer[0] & 0x0f;
  let offset = 12 + csrcCount * 4;
  if (offset > buffer.length) throw new Error('RTP CSRC list exceeds packet length');

  if (hasExtension) {
    if (offset + 4 > buffer.length) throw new Error('Truncated RTP extension header');
    const extensionWords = buffer.readUInt16BE(offset + 2);
    offset += 4 + extensionWords * 4;
    if (offset > buffer.length) throw new Error('RTP extension exceeds packet length');
  }

  let end = buffer.length;
  if (hasPadding) {
    const paddingLength = buffer[buffer.length - 1];
    if (paddingLength === 0 || paddingLength > buffer.length - offset) {
      throw new Error('Invalid RTP padding length');
    }
    end -= paddingLength;
  }

  return {
    version,
    padding: hasPadding,
    extension: hasExtension,
    marker: Boolean(buffer[1] & 0x80),
    payloadType: buffer[1] & 0x7f,
    sequenceNumber: buffer.readUInt16BE(2),
    timestamp: buffer.readUInt32BE(4),
    ssrc: buffer.readUInt32BE(8),
    payload: buffer.subarray(offset, end),
  };
}

module.exports = { encodeRtpPacket, parseRtpPacket };
