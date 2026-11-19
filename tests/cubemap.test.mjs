import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import {
  FACES,
  SEAM_PAIRS,
  convertEquirectToCube,
  directionFromFace,
  faceCoordsFromDirection,
} from "../src/equirect-cube.js";
import { measureCubeSeams, simulateRotationCadence } from "../src/metrics.js";
import { decodePng } from "../scripts/png.mjs";

function syntheticSource(width = 128, height = 64) {
  const data = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const offset = (y * width + x) * 4;
      data[offset] = Math.round(40 + 170 * x / width);
      data[offset + 1] = Math.round(45 + 160 * y / height);
      data[offset + 2] = Math.round(90 + 80 * Math.sin(x / width * Math.PI * 4));
      data[offset + 3] = 255;
    }
  }
  return { width, height, data };
}

test("direction mapping is continuous and invertible", () => {
  for (let face = 0; face < 6; face += 1) {
    for (const s of [-1, -0.63, 0, 0.41, 1]) {
      for (const t of [-1, -0.38, 0, 0.72, 1]) {
        const direction = directionFromFace(face, s, t);
        const projected = faceCoordsFromDirection(face, direction);
        assert.ok(projected);
        assert.ok(Math.abs(projected[0] - s) < 1e-12);
        assert.ok(Math.abs(projected[1] - t) < 1e-12);
      }
    }
  }
});

test("all six faces expose the expected resolution", () => {
  const size = 32;
  const faces = convertEquirectToCube(syntheticSource(), size);
  assert.equal(faces.length, 6);
  for (const face of faces) assert.equal(face.length, size * size * 4);
  assert.deepEqual(FACES.map((face) => face.id), ["px", "nx", "py", "ny", "pz", "nz"]);
});

test("all twelve shared seams have exact zero pixel difference", () => {
  const size = 64;
  const faces = convertEquirectToCube(syntheticSource(), size);
  const metrics = measureCubeSeams(faces, size);
  assert.equal(metrics.pairs.length, 12);
  assert.equal(metrics.maxChannelDelta, 0);
  assert.equal(metrics.maxLumaDelta, 0);
  assert.equal(metrics.mismatchPixels, 0);
  assert.equal(metrics.exactZero, true);
  for (const pair of metrics.pairs) {
    assert.equal(pair.maxChannelDelta, 0, pair.pair);
    assert.equal(pair.mismatchPixels, 0, pair.pair);
  }
  assert.equal(metrics.totalComparisons, SEAM_PAIRS.length * size * 3);
});

test("inter-face reprojection error is below floating-point noise", () => {
  const size = 64;
  const metrics = measureCubeSeams(convertEquirectToCube(syntheticSource(), size), size);
  assert.ok(metrics.maxReprojectionPx < 1e-9);
  assert.ok(metrics.maxAngularErrorDeg < 1e-9);
});

test("fixed-clock rotation updates preserve cadence with no drops", () => {
  const rates = simulateRotationCadence({
    angularVelocityDegPerSec: 30,
    refreshRates: [60, 90, 120],
    durationSeconds: 10,
  });
  assert.equal(rates.length, 3);
  for (const rate of rates) {
    assert.equal(rate.droppedFrames, 0);
    assert.equal(rate.actualUpdates, rate.expectedUpdates);
    assert.equal(rate.degreesPerUpdate, 30 / rate.updateHz);
    assert.equal(rate.maxStepErrorDeg, 0);
  }
});

test("published face assets match the report", async () => {
  const manifest = JSON.parse(await readFile(new URL("../assets/cube/cube-manifest.json", import.meta.url)));
  const report = JSON.parse(await readFile(new URL("../reports/conversion-report.json", import.meta.url)));
  assert.equal(manifest.cube.faces.length, 6);
  assert.equal(report.results.faceResolutions.length, 6);
  assert.equal(report.results.seamError.maxChannelDelta, 0);
  assert.equal(report.results.seamError.mismatchPixels, 0);
  assert.equal(report.cadence.droppedFrames, 0);

  const decodedFaces = [];
  for (const face of manifest.cube.faces) {
    const png = await readFile(new URL(`../${face.url.replace("./", "")}`, import.meta.url));
    assert.equal(png.readUInt32BE(16), face.width);
    assert.equal(png.readUInt32BE(20), face.height);
    decodedFaces.push(decodePng(png));
  }
  const decodedMetrics = measureCubeSeams(decodedFaces.map((face) => face.data), manifest.cube.faceSize);
  assert.equal(decodedMetrics.maxChannelDelta, 0);
  assert.equal(decodedMetrics.maxLumaDelta, 0);
  assert.equal(decodedMetrics.mismatchPixels, 0);
  assert.equal(decodedMetrics.exactZero, true);
});
