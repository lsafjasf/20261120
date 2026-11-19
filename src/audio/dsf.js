'use strict';

const { packBits, unpackBits } = require('./dsd-core');

function channelTypeForCount(channelCount) {
  if (channelCount === 1) return 1;
  if (channelCount === 2) return 2;
  return 6;
}

function dataBytesPerBlock(bits) {
  return Math.ceil(bits.length / 8);
}

function encodeDsf({ channels, sampleRate, blockSize = 4096 }) {
  const channelCount = channels.length;
  const sampleCount = BigInt(channels[0].length);
  const bytesPerChannel = dataBytesPerBlock(channels[0]);
  const blocks = Math.ceil(bytesPerChannel / blockSize);
  const soundData = Buffer.alloc(blocks * blockSize * channelCount);

  for (let block = 0; block < blocks; block += 1) {
    for (let channel = 0; channel < channelCount; channel += 1) {
      const byteStart = block * blockSize;
      const byteEnd = Math.min(byteStart + blockSize, bytesPerChannel);
      const bitStart = byteStart * 8;
      const bitEnd = Math.min(bitStart + blockSize * 8, channels[channel].length);
      const packed = packBits(channels[channel].subarray(bitStart, bitEnd), true);
      packed.copy(soundData, (block * channelCount + channel) * blockSize);
    }
  }

  const dsdHeader = Buffer.alloc(28);
  dsdHeader.write('DSD ', 0, 4, 'ascii');
  dsdHeader.writeBigUInt64LE(28n, 4);

  const fmt = Buffer.alloc(52);
  fmt.write('fmt ', 0, 4, 'ascii');
  fmt.writeBigUInt64LE(52n, 4);
  fmt.writeUInt32LE(1, 12);
  fmt.writeUInt32LE(0, 16);
  fmt.writeUInt32LE(channelTypeForCount(channelCount), 20);
  fmt.writeUInt32LE(channelCount, 24);
  fmt.writeUInt32LE(sampleRate, 28);
  fmt.writeUInt32LE(1, 32);
  fmt.writeBigUInt64LE(sampleCount, 36);
  fmt.writeUInt32LE(blockSize, 44);
  fmt.writeUInt32LE(0, 48);

  const dataHeader = Buffer.alloc(12);
  dataHeader.write('data', 0, 4, 'ascii');
  dataHeader.writeBigUInt64LE(BigInt(soundData.length + 12), 4);

  const totalSize = dsdHeader.length + fmt.length + dataHeader.length + soundData.length;
  dsdHeader.writeBigUInt64LE(BigInt(totalSize), 12);
  dsdHeader.writeBigUInt64LE(0n, 20);

  return Buffer.concat([dsdHeader, fmt, dataHeader, soundData]);
}

function parseDsf(buffer) {
  if (buffer.toString('ascii', 0, 4) !== 'DSD ') throw new Error('Not a DSF DSD file');
  let offset = 0;
  let header = null;
  let format = null;
  let soundData = null;
  let dataChunkSize = null;

  while (offset + 12 <= buffer.length) {
    const id = buffer.toString('ascii', offset, offset + 4);
    const chunkSize = Number(buffer.readBigUInt64LE(offset + 4));
    if (chunkSize < 12 || offset + chunkSize > buffer.length) {
      throw new Error(`Invalid or truncated DSF chunk ${id}`);
    }

    if (id === 'DSD ') {
      if (chunkSize !== 28) throw new Error(`Unexpected DSF header chunk size ${chunkSize}`);
      header = {
        totalFileSize: Number(buffer.readBigUInt64LE(offset + 12)),
        metadataOffset: Number(buffer.readBigUInt64LE(offset + 20)),
      };
    } else if (id === 'fmt ') {
      if (chunkSize !== 52) throw new Error(`Unexpected DSF fmt chunk size ${chunkSize}`);
      format = {
        version: buffer.readUInt32LE(offset + 12),
        formatId: buffer.readUInt32LE(offset + 16),
        channelType: buffer.readUInt32LE(offset + 20),
        channelCount: buffer.readUInt32LE(offset + 24),
        sampleRate: buffer.readUInt32LE(offset + 28),
        bitsPerSample: buffer.readUInt32LE(offset + 32),
        sampleCount: Number(buffer.readBigUInt64LE(offset + 36)),
        blockSize: buffer.readUInt32LE(offset + 44),
        reserved: buffer.readUInt32LE(offset + 48),
      };
    } else if (id === 'data') {
      dataChunkSize = chunkSize;
      soundData = buffer.subarray(offset + 12, offset + chunkSize);
    }
    offset += chunkSize;
  }

  if (!header || !format || !soundData) {
    throw new Error('DSF is missing the DSD, fmt, or data chunk');
  }
  if (format.version !== 1 || format.formatId !== 0) {
    throw new Error(`Unsupported DSF version/id ${format.version}/${format.formatId}`);
  }
  if (format.bitsPerSample !== 1) {
    throw new Error(`Unsupported DSF sample width ${format.bitsPerSample}`);
  }

  const channels = Array.from(
    { length: format.channelCount },
    () => new Uint8Array(format.sampleCount),
  );
  const bytesPerChannel = Math.ceil(format.sampleCount / 8);
  const blocks = Math.ceil(bytesPerChannel / format.blockSize);

  for (let block = 0; block < blocks; block += 1) {
    const byteStart = block * format.blockSize;
    const byteCount = Math.min(format.blockSize, bytesPerChannel - byteStart);
    for (let channel = 0; channel < format.channelCount; channel += 1) {
      const sourceOffset = (block * format.channelCount + channel) * format.blockSize;
      if (sourceOffset + byteCount > soundData.length) {
        throw new Error('DSF block table exceeds the data chunk');
      }
      const bits = unpackBits(soundData.subarray(sourceOffset, sourceOffset + byteCount), byteCount * 8, true);
      const targetOffset = byteStart * 8;
      const targetCount = Math.min(bits.length, format.sampleCount - targetOffset);
      channels[channel].set(bits.subarray(0, targetCount), targetOffset);
    }
  }

  return {
    container: 'DSF',
    sampleRate: format.sampleRate,
    channelCount: format.channelCount,
    bitCount: format.sampleCount,
    bytes: buffer.length,
    blockSize: format.blockSize,
    dataChunkSize,
    header,
    format,
    channels,
  };
}

module.exports = { encodeDsf, parseDsf };
