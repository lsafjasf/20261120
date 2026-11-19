import { readFile } from "node:fs/promises";

export async function readPcm16Wav(filePath) {
  const bytes = await readFile(filePath);
  if (bytes.toString("ascii", 0, 4) !== "RIFF") {
    throw new Error(`${filePath}: not a RIFF file`);
  }
  if (bytes.toString("ascii", 8, 12) !== "WAVE") {
    throw new Error(`${filePath}: not a WAVE file`);
  }

  let format = null;
  let data = null;
  let offset = 12;
  while (offset + 8 <= bytes.length) {
    const id = bytes.toString("ascii", offset, offset + 4);
    const size = bytes.readUInt32LE(offset + 4);
    const body = offset + 8;
    if (body + size > bytes.length) {
      throw new Error(`${filePath}: truncated ${id} chunk`);
    }
    if (id === "fmt ") format = parseFormat(bytes, body, size);
    if (id === "data") data = bytes.subarray(body, body + size);
    offset = body + size + (size & 1);
  }

  if (!format) throw new Error(`${filePath}: missing fmt chunk`);
  if (!data) throw new Error(`${filePath}: missing data chunk`);
  if (format.audioFormat !== 1) {
    throw new Error(`${filePath}: expected PCM, got format ${format.audioFormat}`);
  }
  if (format.channels !== 1 || format.bitsPerSample !== 16) {
    throw new Error(`${filePath}: expected mono 16-bit PCM`);
  }
  if (data.length % 2 !== 0) throw new Error(`${filePath}: odd PCM data length`);

  const samples = new Int16Array(data.length / 2);
  for (let index = 0; index < samples.length; index += 1) {
    samples[index] = data.readInt16LE(index * 2);
  }
  return {
    samples,
    sampleRate: format.sampleRate,
    channels: format.channels,
    bitsPerSample: format.bitsPerSample,
  };
}

function parseFormat(bytes, offset, size) {
  if (size < 16) throw new Error("invalid fmt chunk");
  return {
    audioFormat: bytes.readUInt16LE(offset),
    channels: bytes.readUInt16LE(offset + 2),
    sampleRate: bytes.readUInt32LE(offset + 4),
    bitsPerSample: bytes.readUInt16LE(offset + 14),
  };
}
