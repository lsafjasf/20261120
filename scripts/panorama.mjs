const TAU = Math.PI * 2;

function clamp(value, min, max) {
  return value < min ? min : value > max ? max : value;
}

function smoothstep(edge0, edge1, value) {
  const t = clamp((value - edge0) / (edge1 - edge0), 0, 1);
  return t * t * (3 - 2 * t);
}

function mix(a, b, amount) {
  return a + (b - a) * amount;
}

function mixColor(a, b, amount) {
  return [
    mix(a[0], b[0], amount),
    mix(a[1], b[1], amount),
    mix(a[2], b[2], amount),
  ];
}

function addColor(base, color, amount) {
  return [
    base[0] + color[0] * amount,
    base[1] + color[1] * amount,
    base[2] + color[2] * amount,
  ];
}

function angularDistance(a, b) {
  return Math.abs(Math.atan2(Math.sin(a - b), Math.cos(a - b)));
}

function ridgeHeight(azimuth, layer) {
  const phase = layer * 1.73;
  return 0.023
    + layer * 0.012
    + 0.020 * Math.sin(azimuth * 3 + phase)
    + 0.011 * Math.sin(azimuth * 7 - phase * 1.8)
    + 0.005 * Math.sin(azimuth * 13 + phase * 0.6);
}

function renderSky(lon, lat, x, y, z) {
  const above = smoothstep(-0.04, 0.42, y);
  let color = mixColor([4, 10, 22], [26, 70, 104], above);
  color = mixColor(color, [101, 174, 184], smoothstep(0.09, 0.006, Math.abs(y)) * 0.5);

  const sunLon = -0.72;
  const sunLat = 0.19;
  const sunDirection = [
    Math.cos(sunLat) * Math.cos(sunLon),
    Math.sin(sunLat),
    Math.cos(sunLat) * Math.sin(sunLon),
  ];
  const sunDot = clamp(x * sunDirection[0] + y * sunDirection[1] + z * sunDirection[2], -1, 1);
  const sunAngle = Math.acos(sunDot);
  const glow = Math.exp(-sunAngle * 7.5);
  const disc = smoothstep(0.031, 0.024, sunAngle);
  color = addColor(color, [255, 146, 63], glow * 0.64);
  color = addColor(color, [255, 239, 190], disc * 1.25);

  const horizonGlow = smoothstep(0.2, 0.0, Math.abs(y)) * smoothstep(0.3, 1.0, Math.cos(angularDistance(lon, sunLon) * 0.65));
  color = addColor(color, [255, 126, 63], horizonGlow * 0.2);

  for (let layer = 0; layer < 3; layer += 1) {
    const height = ridgeHeight(lon, layer);
    const inside = smoothstep(height, height - 0.0015, y);
    const base = mixColor([7, 22, 30], [16, 43, 51], layer / 3);
    color = mixColor(color, base, inside * (0.91 - layer * 0.08));
    const rim = Math.exp(-Math.abs(y - height) * 450) * (y < height + 0.01 ? 1 : 0);
    color = addColor(color, [72, 187, 186], rim * (0.10 + layer * 0.04));
  }

  const water = smoothstep(-0.002, -0.065, y);
  const waterColor = mixColor([4, 13, 22], [5, 34, 42], smoothstep(-0.7, 0, y));
  color = mixColor(color, waterColor, water);
  const wave = Math.sin(lon * 22 + y * 95) * Math.sin(lon * 9 - y * 47);
  const shimmer = smoothstep(0.75, 1, wave) * smoothstep(-0.32, -0.03, y) * water;
  color = addColor(color, [28, 136, 137], shimmer * 0.16);

  const lonGrid = Math.exp(-Math.pow(angularDistance(lon, Math.round(lon / (Math.PI / 6)) * (Math.PI / 6)) * 620, 2));
  const latGrid = Math.exp(-Math.pow((lat - Math.round(lat / (Math.PI / 12)) * (Math.PI / 12)) * 620, 2));
  const gridMask = Math.max(lonGrid, latGrid) * 0.07;
  color = addColor(color, [80, 198, 194], gridMask);

  for (let index = 0; index < 8; index += 1) {
    const azimuth = index * TAU / 8;
    const distance = angularDistance(lon, azimuth);
    const pillar = Math.exp(-Math.pow(distance * 850, 2)) * smoothstep(0.14, -0.005, y) * 0.45;
    const beacon = Math.exp(-Math.pow(distance * 500, 2)) * Math.exp(-Math.pow((y - 0.018) * 170, 2));
    color = addColor(color, index === 0 ? [255, 142, 82] : [66, 220, 204], pillar * 0.18 + beacon);
  }

  const vignette = 1 - 0.16 * smoothstep(0.72, 1, Math.abs(y));
  return color.map((channel) => clamp(Math.round(channel * vignette), 0, 255));
}

export function createProceduralEquirect(width = 2048, height = 1024) {
  const data = new Uint8ClampedArray(width * height * 4);
  for (let py = 0; py < height; py += 1) {
    const v = (py + 0.5) / height;
    const lat = (0.5 - v) * Math.PI;
    const cosLat = Math.cos(lat);
    const y = Math.sin(lat);
    for (let px = 0; px < width; px += 1) {
      const u = (px + 0.5) / width;
      const lon = (u - 0.5) * TAU;
      const x = cosLat * Math.cos(lon);
      const z = cosLat * Math.sin(lon);
      const color = renderSky(lon, lat, x, y, z);
      const offset = (py * width + px) * 4;
      data[offset] = color[0];
      data[offset + 1] = color[1];
      data[offset + 2] = color[2];
      data[offset + 3] = 255;
    }
  }
  return { width, height, data, name: "procedural-orbit-horizon" };
}
