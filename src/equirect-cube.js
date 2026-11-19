export const FACE_IDS = ["px", "nx", "py", "ny", "pz", "nz"];

export const FACES = [
  { id: "px", label: "+X", name: "Right", accent: "#ff7a59" },
  { id: "nx", label: "-X", name: "Left", accent: "#ffbd59" },
  { id: "py", label: "+Y", name: "Top", accent: "#55d6be" },
  { id: "ny", label: "-Y", name: "Bottom", accent: "#5ba8ff" },
  { id: "pz", label: "+Z", name: "Front", accent: "#c38cff" },
  { id: "nz", label: "-Z", name: "Back", accent: "#ff72b6" },
];

export const SEAM_PAIRS = [
  { a: [0, "left"], b: [4, "right"], reverse: false, label: "+X left / +Z right" },
  { a: [0, "right"], b: [5, "left"], reverse: false, label: "+X right / -Z left" },
  { a: [0, "top"], b: [2, "right"], reverse: true, label: "+X top / +Y right" },
  { a: [0, "bottom"], b: [3, "right"], reverse: false, label: "+X bottom / -Y right" },
  { a: [1, "left"], b: [5, "right"], reverse: false, label: "-X left / -Z right" },
  { a: [1, "right"], b: [4, "left"], reverse: false, label: "-X right / +Z left" },
  { a: [1, "top"], b: [2, "left"], reverse: false, label: "-X top / +Y left" },
  { a: [1, "bottom"], b: [3, "left"], reverse: true, label: "-X bottom / -Y left" },
  { a: [2, "top"], b: [5, "top"], reverse: true, label: "+Y top / -Z top" },
  { a: [2, "bottom"], b: [4, "top"], reverse: false, label: "+Y bottom / +Z top" },
  { a: [3, "top"], b: [4, "bottom"], reverse: false, label: "-Y top / +Z bottom" },
  { a: [3, "bottom"], b: [5, "bottom"], reverse: true, label: "-Y bottom / -Z bottom" },
];

function clamp(value, min, max) {
  return value < min ? min : value > max ? max : value;
}

export function directionFromFace(faceIndex, s, t) {
  switch (faceIndex) {
    case 0: return [1, -t, -s];
    case 1: return [-1, -t, s];
    case 2: return [s, 1, t];
    case 3: return [s, -1, -t];
    case 4: return [s, -t, 1];
    case 5: return [-s, -t, -1];
    default: throw new RangeError(`Unknown cube face: ${faceIndex}`);
  }
}

export function faceCoordsFromDirection(faceIndex, direction) {
  const [x, y, z] = direction;
  const ax = Math.abs(x);
  const ay = Math.abs(y);
  const az = Math.abs(z);
  if (faceIndex === 0 && ax >= ay && ax >= az) return [-z / ax, -y / ax];
  if (faceIndex === 1 && ax >= ay && ax >= az) return [z / ax, -y / ax];
  if (faceIndex === 2 && ay >= ax && ay >= az) return [x / ay, z / ay];
  if (faceIndex === 3 && ay >= ax && ay >= az) return [x / ay, -z / ay];
  if (faceIndex === 4 && az >= ax && az >= ay) return [x / az, -y / az];
  if (faceIndex === 5 && az >= ax && az >= ay) return [-x / az, -y / az];
  return null;
}

export function directionToEquirect(direction) {
  const [x, y, z] = direction;
  const length = Math.hypot(x, y, z) || 1;
  const nx = x / length;
  const ny = clamp(y / length, -1, 1);
  const nz = z / length;
  return {
    u: Math.atan2(nz, nx) / (Math.PI * 2) + 0.5,
    v: 0.5 - Math.asin(ny) / Math.PI,
  };
}

function normalizePixel(value, size) {
  return -1 + (2 * value) / (size - 1);
}

export function facePixelDirection(faceIndex, x, y, size) {
  return directionFromFace(faceIndex, normalizePixel(x, size), normalizePixel(y, size));
}

export function edgePixel(edge, index, size) {
  switch (edge) {
    case "left": return [0, index];
    case "right": return [size - 1, index];
    case "top": return [index, 0];
    case "bottom": return [index, size - 1];
    default: throw new RangeError(`Unknown edge: ${edge}`);
  }
}

export function sampleEquirect(source, u, v, wrap = true) {
  const { width, height, data } = source;
  const x = u * width - 0.5;
  const y = clamp(v, 0, 1) * height - 0.5;
  const x0 = Math.floor(x);
  const y0 = Math.floor(y);
  const fx = x - x0;
  const fy = y - y0;
  const y1 = clamp(y0 + 1, 0, height - 1);
  const wrapX = (value) => {
    if (!wrap) return clamp(value, 0, width - 1);
    return ((value % width) + width) % width;
  };
  const xa = wrapX(x0);
  const xb = wrapX(x0 + 1);
  const yc = clamp(y0, 0, height - 1);
  const i00 = (yc * width + xa) * 4;
  const i10 = (yc * width + xb) * 4;
  const i01 = (y1 * width + xa) * 4;
  const i11 = (y1 * width + xb) * 4;
  const out = [0, 0, 0, 255];
  for (let channel = 0; channel < 4; channel += 1) {
    const top = data[i00 + channel] * (1 - fx) + data[i10 + channel] * fx;
    const bottom = data[i01 + channel] * (1 - fx) + data[i11 + channel] * fx;
    out[channel] = Math.round(top * (1 - fy) + bottom * fy);
  }
  return out;
}

export function convertEquirectToCube(source, faceSize = 512) {
  if (!source?.data || source.width < 2 || source.height < 2) {
    throw new TypeError("A decoded RGBA equirectangular image is required.");
  }
  if (!Number.isInteger(faceSize) || faceSize < 2) {
    throw new RangeError("faceSize must be an integer greater than one.");
  }
  const faces = FACE_IDS.map(() => new Uint8ClampedArray(faceSize * faceSize * 4));
  for (let faceIndex = 0; faceIndex < 6; faceIndex += 1) {
    const faceData = faces[faceIndex];
    for (let y = 0; y < faceSize; y += 1) {
      const t = normalizePixel(y, faceSize);
      for (let x = 0; x < faceSize; x += 1) {
        const s = normalizePixel(x, faceSize);
        const direction = directionFromFace(faceIndex, s, t);
        const uv = directionToEquirect(direction);
        const rgba = sampleEquirect(source, uv.u, uv.v);
        const offset = (y * faceSize + x) * 4;
        faceData[offset] = rgba[0];
        faceData[offset + 1] = rgba[1];
        faceData[offset + 2] = rgba[2];
        faceData[offset + 3] = 255;
      }
    }
  }
  alignCubeSeams(faces, faceSize);
  return faces;
}

function copyPixel(faces, size, fromFace, fromX, fromY, toFace, toX, toY) {
  const from = (fromY * size + fromX) * 4;
  const to = (toY * size + toX) * 4;
  const source = faces[fromFace];
  const target = faces[toFace];
  target[to] = source[from];
  target[to + 1] = source[from + 1];
  target[to + 2] = source[from + 2];
  target[to + 3] = source[from + 3];
}

export function alignCubeSeams(faces, size) {
  for (const pair of SEAM_PAIRS) {
    const [faceA, edgeA] = pair.a;
    const [faceB, edgeB] = pair.b;
    for (let i = 0; i < size; i += 1) {
      const [ax, ay] = edgePixel(edgeA, i, size);
      const mapped = pair.reverse ? size - 1 - i : i;
      const [bx, by] = edgePixel(edgeB, mapped, size);
      copyPixel(faces, size, faceA, ax, ay, faceB, bx, by);
    }
  }
  synchronizeCubeCorners(faces, size);
  return faces;
}

function synchronizeCubeCorners(faces, size) {
  const groups = new Map();
  for (let face = 0; face < 6; face += 1) {
    for (const x of [0, size - 1]) {
      for (const y of [0, size - 1]) {
        const direction = facePixelDirection(face, x, y, size);
        const key = direction.map((value) => (value < 0 ? "-" : "+")).join("");
        const group = groups.get(key) ?? [];
        group.push([face, x, y]);
        groups.set(key, group);
      }
    }
  }
  for (const group of groups.values()) {
    const [face, x, y] = group[0];
    for (let i = 1; i < group.length; i += 1) {
      const [otherFace, otherX, otherY] = group[i];
      copyPixel(faces, size, face, x, y, otherFace, otherX, otherY);
    }
  }
}
