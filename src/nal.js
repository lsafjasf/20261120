// H.264 NAL unit helpers: type names, Annex-B splitting and slice header parsing.
//
// NAL unit type codes (RFC 6184 section 5.1.3 / ITU-T H.264 section 7.4.1).
export const NAL_TYPES = {
  1: 'SLICE (non-IDR VCL)',
  2: 'DPA',
  3: 'DPB',
  4: 'DPC',
  5: 'IDR slice',
  6: 'SEI',
  7: 'SPS',
  8: 'PPS',
  9: 'AUD',
  10: 'End of sequence',
  11: 'End of stream',
  12: 'Filler',
  24: 'STAP-A',
  25: 'STAP-B',
  26: 'MTAP16',
  27: 'MTAP24',
  28: 'FU-A',
  29: 'FU-B',
};

export const SLICE_TYPE_NAMES = ['P', 'B', 'I', 'SP', 'SI', 'P', 'B', 'I', 'SP', 'SI'];

export function nalType(nal) {
  return nal[0] & 0x1f;
}

export function nalTypeName(type) {
  return NAL_TYPES[type] || `reserved (${type})`;
}

export function isVclNal(type) {
  return type >= 1 && type <= 5;
}

// Split an Annex-B byte stream (00 00 01 / 00 00 00 01 start codes) into NAL units.
export function splitAnnexB(bytes) {
  // Pass 1: locate each 00 00 01 marker (possibly preceded by extra leading zeros).
  const markers = [];
  let zeroRun = 0;
  for (let i = 0; i < bytes.length; i += 1) {
    if (bytes[i] === 0) {
      zeroRun += 1;
    } else if (bytes[i] === 1 && zeroRun >= 2) {
      markers.push({ nalStart: i + 1, markerStart: i - zeroRun });
      zeroRun = 0;
    } else {
      zeroRun = 0;
    }
  }
  // Pass 2: each NAL spans from its NAL start to the start of the next marker.
  const nals = [];
  for (let s = 0; s < markers.length; s += 1) {
    const start = markers[s].nalStart;
    const end = s + 1 < markers.length ? markers[s + 1].markerStart : bytes.length;
    if (end > start) nals.push(bytes.subarray(start, end));
  }
  return nals;
}

// Exp-Golomb unsigned reader (ITU-T H.264 9.1) over a bit position.
function readUe(bytes, bitPos) {
  let zeros = 0;
  const totalBits = bytes.length * 8;
  while (bitPos < totalBits) {
    const bit = (bytes[bitPos >> 3] >> (7 - (bitPos & 7))) & 1;
    bitPos += 1;
    if (bit === 1) break;
    zeros += 1;
    if (zeros > 32) throw new RangeError('ue(v) run too long');
  }
  let value = 1;
  for (let k = 0; k < zeros; k += 1) {
    const bit = (bytes[bitPos >> 3] >> (7 - (bitPos & 7))) & 1;
    bitPos += 1;
    value = (value << 1) | bit;
  }
  return { value: value - 1, bitsRead: zeros * 2 + 1 };
}

// Parse the leading fields of a VCL slice header (H.264 7.3.3).
// Returns first_mb_in_slice (slice index) and slice_type; null for non-VCL NALs.
export function parseSliceHeader(nal) {
  const type = nalType(nal);
  if (!isVclNal(type)) return null;
  let bitPos = 8; // skip NAL header byte
  const firstMb = readUe(nal, bitPos);
  bitPos += firstMb.bitsRead;
  const sliceType = readUe(nal, bitPos);
  bitPos += sliceType.bitsRead;
  const ppsId = readUe(nal, bitPos);
  return {
    sliceIndex: firstMb.value,
    sliceType: sliceType.value,
    sliceTypeName: SLICE_TYPE_NAMES[sliceType.value] || '?',
    ppsId: ppsId.value,
  };
}
