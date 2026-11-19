// Synthetic NAL units with parseable H.264 slice headers (Exp-Golomb in slice header).

function fillRest(nal, seed) {
  for (let i = 2; i < nal.length; i += 1) {
    nal[i] = 0x20 + ((seed + i * 13) & 0x1f); // avoid 0x00/0x01 so no false Annex-B start codes
  }
}

// IDR I-slice: first_mb_in_slice=0 (ue=1), slice_type=I=2 (ue=011), pps_id=0 (ue=1)
// header bits after NAL byte: 1 011 1 -> 0b10111000 = 0xB8
export function makeIdrSlice(length = 50) {
  const nal = new Uint8Array(length);
  nal[0] = 0x65; // ref=3, type=5 (IDR)
  nal[1] = 0xB8;
  fillRest(nal, 5);
  return nal;
}

// P-slice, first_mb_in_slice=0: bits 1 1 1 -> 0b11100000 = 0xE0
export function makePSlice0(length = 50) {
  const nal = new Uint8Array(length);
  nal[0] = 0x41; // ref=2, type=1 (non-IDR VCL)
  nal[1] = 0xE0;
  fillRest(nal, 1);
  return nal;
}

// P-slice, first_mb_in_slice=1: ue(1)=010, then 1, 1 -> 0b01011000 = 0x58
export function makePSlice1(length = 50) {
  const nal = new Uint8Array(length);
  nal[0] = 0x41;
  nal[1] = 0x58;
  fillRest(nal, 2);
  return nal;
}

export function makeSps(length = 18) {
  const nal = new Uint8Array(length);
  nal[0] = 0x67;
  fillRest(nal, 7);
  return nal;
}

export function makePps(length = 10) {
  const nal = new Uint8Array(length);
  nal[0] = 0x68;
  fillRest(nal, 8);
  return nal;
}

export function makeAud(length = 6) {
  const nal = new Uint8Array(length);
  nal[0] = 0x09;
  fillRest(nal, 9);
  return nal;
}

export function bytesEqual(a, b) {
  return a.length === b.length && a.every((value, i) => value === b[i]);
}
