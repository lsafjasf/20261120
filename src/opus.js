// Opus packet TOC inspection (RFC 6716 section 3.1) and decode-path routing.
//
// TOC byte bits: config(5) | s(1) | c(2)
//   configs 0..11 -> SILK-only mode
//   configs 12..15 -> Hybrid (CELT+SILK) mode
//   configs 16..31 -> CELT-only mode

export const OPUS_BANDWIDTH = {
  narrowband: 'NB',
  mediumband: 'MB',
  wideband: 'WB',
  superwideband: 'SWB',
  fullband: 'FB',
};

// Per-config table from RFC 6716 tables 1-2.
const CONFIG_TABLE = [
  { mode: 'SILK', bandwidth: 'NB', frameMs: 10 },
  { mode: 'SILK', bandwidth: 'NB', frameMs: 20 },
  { mode: 'SILK', bandwidth: 'NB', frameMs: 40 },
  { mode: 'SILK', bandwidth: 'NB', frameMs: 60 },
  { mode: 'SILK', bandwidth: 'MB', frameMs: 10 },
  { mode: 'SILK', bandwidth: 'MB', frameMs: 20 },
  { mode: 'SILK', bandwidth: 'MB', frameMs: 40 },
  { mode: 'SILK', bandwidth: 'MB', frameMs: 60 },
  { mode: 'SILK', bandwidth: 'WB', frameMs: 10 },
  { mode: 'SILK', bandwidth: 'WB', frameMs: 20 },
  { mode: 'SILK', bandwidth: 'WB', frameMs: 40 },
  { mode: 'SILK', bandwidth: 'WB', frameMs: 60 },
  { mode: 'HYBRID', bandwidth: 'SWB', frameMs: 10 },
  { mode: 'HYBRID', bandwidth: 'SWB', frameMs: 20 },
  { mode: 'HYBRID', bandwidth: 'FB', frameMs: 10 },
  { mode: 'HYBRID', bandwidth: 'FB', frameMs: 20 },
  { mode: 'CELT', bandwidth: 'NB', frameMs: 2.5 },
  { mode: 'CELT', bandwidth: 'NB', frameMs: 5 },
  { mode: 'CELT', bandwidth: 'NB', frameMs: 10 },
  { mode: 'CELT', bandwidth: 'NB', frameMs: 20 },
  { mode: 'CELT', bandwidth: 'WB', frameMs: 2.5 },
  { mode: 'CELT', bandwidth: 'WB', frameMs: 5 },
  { mode: 'CELT', bandwidth: 'WB', frameMs: 10 },
  { mode: 'CELT', bandwidth: 'WB', frameMs: 20 },
  { mode: 'CELT', bandwidth: 'SWB', frameMs: 2.5 },
  { mode: 'CELT', bandwidth: 'SWB', frameMs: 5 },
  { mode: 'CELT', bandwidth: 'SWB', frameMs: 10 },
  { mode: 'CELT', bandwidth: 'SWB', frameMs: 20 },
  { mode: 'CELT', bandwidth: 'FB', frameMs: 2.5 },
  { mode: 'CELT', bandwidth: 'FB', frameMs: 5 },
  { mode: 'CELT', bandwidth: 'FB', frameMs: 10 },
  { mode: 'CELT', bandwidth: 'FB', frameMs: 20 },
];

export function parseOpusToc(packet) {
  if (!packet || packet.length === 0) throw new RangeError('empty Opus packet');
  const toc = packet[0];
  const config = toc >> 3;
  const stereo = ((toc >> 2) & 1) === 1;
  const code = toc & 3;
  const frameCount = code === 0 ? 1 : code === 1 ? 2 : code === 2 ? 2 : 0; // 3 => arbitrary count
  return {
    toc,
    config,
    stereo,
    code,
    frameCount,
    ...CONFIG_TABLE[config],
    decodePath: config <= 11 ? 'SILK' : config <= 15 ? 'HYBRID' : 'CELT',
  };
}

// Routes a parsed Opus packet onto the correct decoder implementation name.
export function routeOpusPacket(packet) {
  const info = parseOpusToc(packet);
  const decoder = info.decodePath === 'SILK'
    ? 'opus-silk-decoder'
    : info.decodePath === 'HYBRID'
      ? 'opus-hybrid-decoder'
      : 'opus-celt-decoder';
  return { ...info, decoder };
}

// Build a synthetic Opus packet with a chosen TOC config plus dummy payload bytes.
export function makeOpusPacket(config, { stereo = false, code = 0, payloadBytes = 20 } = {}) {
  const toc = ((config & 31) << 3) | ((stereo ? 1 : 0) << 2) | (code & 3);
  const data = new Uint8Array(1 + payloadBytes);
  data[0] = toc;
  for (let i = 1; i < data.length; i += 1) data[i] = (i * 7 + config) & 0xff;
  return data;
}
