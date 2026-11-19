function writeAscii(view, offset, text) {
  for (let index = 0; index < text.length; index += 1) {
    view.setUint8(offset + index, text.charCodeAt(index));
  }
}

export function encodeWav24(frames, sampleRate) {
  const channels = 2;
  const bytesPerSample = 3;
  const dataBytes = frames.length * channels * bytesPerSample;
  const buffer = new ArrayBuffer(44 + dataBytes);
  const view = new DataView(buffer);
  writeAscii(view, 0, "RIFF");
  view.setUint32(4, 36 + dataBytes, true);
  writeAscii(view, 8, "WAVE");
  writeAscii(view, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, channels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * channels * bytesPerSample, true);
  view.setUint16(32, channels * bytesPerSample, true);
  view.setUint16(34, 24, true);
  writeAscii(view, 36, "data");
  view.setUint32(40, dataBytes, true);

  let offset = 44;
  for (const frame of frames) {
    for (const sample of [frame.left, frame.right]) {
      const word = Number(sample) & 0xffffff;
      view.setUint8(offset, word & 0xff);
      view.setUint8(offset + 1, (word >> 8) & 0xff);
      view.setUint8(offset + 2, (word >> 16) & 0xff);
      offset += 3;
    }
  }
  return new Uint8Array(buffer);
}

export function errorsToCsv(errors) {
  const columns = [
    "id", "type", "severity", "time_s", "end_s", "frame", "channel",
    "title", "detail", "value", "unit"
  ];
  const escape = (value) => {
    if (value === null || value === undefined) return "";
    const text = String(value);
    return /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
  };
  const rows = [columns.join(",")];
  for (const error of errors) {
    rows.push(columns.map((column) => escape(error[column])).join(","));
  }
  return `\uFEFF${rows.join("\n")}\n`;
}

export function downloadBlob(filename, blob) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
