'use strict';

const { unpackBits } = require('./dsd-core');

function encodeChunk(id, data) {
  const padding = data.length % 2;
  const output = Buffer.alloc(12 + data.length + padding);
  output.write(id, 0, 4, 'ascii');
  output.writeBigUInt64BE(BigInt(data.length), 4);
  data.copy(output, 12);
  return output;
}

function channelId(channel, channelCount) {
  if (channelCount === 2) return channel === 0 ? 'SLFT' : 'SRGT';
  return `C${String(channel + 1).padStart(3, '0')}`;
}

function encodeDff({ channels, sampleRate }) {
  const channelCount = channels.length;
  const bytesPerChannel = Math.ceil(channels[0].length / 8);
  const soundData = Buffer.alloc(bytesPerChannel * channelCount);

  for (let byteIndex = 0; byteIndex < bytesPerChannel; byteIndex += 1) {
    for (let channel = 0; channel < channelCount; channel += 1) {
      let value = 0;
      for (let bit = 0; bit < 8; bit += 1) {
        const sourceIndex = byteIndex * 8 + bit;
        if (channels[channel][sourceIndex]) value |= 1 << (7 - bit);
      }
      soundData[byteIndex * channelCount + channel] = value;
    }
  }

  const fver = Buffer.alloc(4);
  fver.writeUInt32BE(0x01050000, 0);

  const fs = Buffer.alloc(4);
  fs.writeUInt32BE(sampleRate, 0);

  const chnl = Buffer.alloc(2 + channelCount * 2);
  chnl.writeUInt16BE(channelCount, 0);
  for (let channel = 0; channel < channelCount; channel += 1) {
    chnl.write(channelId(channel, channelCount), 2 + channel * 2, 2, 'ascii');
  }

  const compressionName = Buffer.from('not compressed', 'ascii');
  const cmpr = Buffer.alloc(5 + compressionName.length);
  cmpr.write('DSD ', 0, 4, 'ascii');
  cmpr[4] = compressionName.length;
  compressionName.copy(cmpr, 5);

  const sndBody = Buffer.concat([
    Buffer.from('SND ', 'ascii'),
    encodeChunk('FS  ', fs),
    encodeChunk('CHNL', chnl),
    encodeChunk('CMPR', cmpr),
  ]);
  const chunks = Buffer.concat([
    encodeChunk('FVER', fver),
    encodeChunk('PROP', sndBody),
    encodeChunk('DSD ', soundData),
  ]);

  const header = Buffer.alloc(16);
  header.write('FRM8', 0, 4, 'ascii');
  header.writeBigUInt64BE(BigInt(chunks.length + 4), 4);
  Buffer.from('DSD ', 'ascii').copy(header, 12);
  return Buffer.concat([header, chunks]);
}

function readChunk(buffer, offset) {
  if (offset + 12 > buffer.length) throw new Error('Truncated DSDIFF chunk header');
  const id = buffer.toString('ascii', offset, offset + 4);
  const size = Number(buffer.readBigUInt64BE(offset + 4));
  const dataStart = offset + 12;
  const dataEnd = dataStart + size;
  if (dataEnd > buffer.length) throw new Error(`Truncated DSDIFF chunk ${id}`);
  return {
    id,
    size,
    data: buffer.subarray(dataStart, dataEnd),
    nextOffset: dataEnd + (size % 2),
  };
}

function parseDff(buffer) {
  if (buffer.toString('ascii', 0, 4) !== 'FRM8') throw new Error('Not a DSDIFF FRM8 file');
  if (buffer.toString('ascii', 12, 16) !== 'DSD ') throw new Error('DSDIFF form is not DSD ');
  const declaredSize = Number(buffer.readBigUInt64BE(4));
  if (declaredSize + 12 > buffer.length) throw new Error('Truncated DSDIFF form');

  let sampleRate = null;
  let channelIds = [];
  let soundData = null;
  let compression = null;
  let offset = 16;
  const formEnd = 12 + declaredSize;

  while (offset < formEnd) {
    const chunk = readChunk(buffer, offset);
    if (chunk.id === 'PROP' && chunk.data.toString('ascii', 0, 4) === 'SND ') {
      let propertyOffset = 4;
      while (propertyOffset < chunk.data.length) {
        const property = readChunk(chunk.data, propertyOffset);
        if (property.id === 'FS  ') sampleRate = property.data.readUInt32BE(0);
        if (property.id === 'CHNL') {
          const count = property.data.readUInt16BE(0);
          channelIds = Array.from({ length: count }, (_, index) =>
            property.data.toString('ascii', 2 + index * 2, 4 + index * 2));
        }
        if (property.id === 'CMPR') {
          compression = property.data.toString('ascii', 0, 4);
        }
        propertyOffset = property.nextOffset;
      }
    }
    if (chunk.id === 'DSD ') soundData = chunk.data;
    offset = chunk.nextOffset;
  }

  if (!sampleRate || !soundData || channelIds.length === 0) {
    throw new Error('DSDIFF is missing FS, CHNL, or DSD data');
  }
  if (compression !== 'DSD ') throw new Error(`Unsupported DSDIFF compression ${compression}`);

  const channelCount = channelIds.length;
  if (soundData.length % channelCount !== 0) {
    throw new Error('DSDIFF interleaved data is not divisible by channel count');
  }
  const bytesPerChannel = soundData.length / channelCount;
  const bitCount = bytesPerChannel * 8;
  const channels = Array.from({ length: channelCount }, () => new Uint8Array(bitCount));
  for (let byteIndex = 0; byteIndex < bytesPerChannel; byteIndex += 1) {
    for (let channel = 0; channel < channelCount; channel += 1) {
      const byte = soundData[byteIndex * channelCount + channel];
      const bits = unpackBits(Buffer.from([byte]), 8, false);
      channels[channel].set(bits, byteIndex * 8);
    }
  }

  return {
    container: 'DFF',
    sampleRate,
    channelCount,
    channelIds,
    bitCount,
    bytes: buffer.length,
    channels,
  };
}

module.exports = { encodeDff, parseDff };
