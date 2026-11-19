import {
  FACES,
  SEAM_PAIRS,
  edgePixel,
  faceCoordsFromDirection,
  facePixelDirection,
} from "./equirect-cube.js";

function luminance(r, g, b) {
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function wrapPixel(value, size) {
  return Math.max(0, Math.min(size - 1, value));
}

function measurePair(faces, size, pair) {
  const [faceA, edgeA] = pair.a;
  const [faceB, edgeB] = pair.b;
  let maxChannelDelta = 0;
  let maxLumaDelta = 0;
  let absoluteChannelDelta = 0;
  let sumSquaredChannelDelta = 0;
  let maxReprojectionPx = 0;
  let sumReprojectionPx = 0;
  let maxAngularErrorDeg = 0;
  let mismatchPixels = 0;
  const channels = size * 4;

  for (let i = 0; i < size; i += 1) {
    const [ax, ay] = edgePixel(edgeA, i, size);
    const [bx, by] = edgePixel(edgeB, pair.reverse ? size - 1 - i : i, size);
    const aOffset = (ay * size + ax) * 4;
    const bOffset = (by * size + bx) * 4;
    let pixelMismatch = false;
    for (let channel = 0; channel < 3; channel += 1) {
      const delta = Math.abs(faces[faceA][aOffset + channel] - faces[faceB][bOffset + channel]);
      maxChannelDelta = Math.max(maxChannelDelta, delta);
      absoluteChannelDelta += delta;
      sumSquaredChannelDelta += delta * delta;
      if (delta !== 0) pixelMismatch = true;
    }
    const lumaA = luminance(
      faces[faceA][aOffset],
      faces[faceA][aOffset + 1],
      faces[faceA][aOffset + 2],
    );
    const lumaB = luminance(
      faces[faceB][bOffset],
      faces[faceB][bOffset + 1],
      faces[faceB][bOffset + 2],
    );
    maxLumaDelta = Math.max(maxLumaDelta, Math.abs(lumaA - lumaB));

    const directionA = facePixelDirection(faceA, ax, ay, size);
    const directionB = facePixelDirection(faceB, bx, by, size);
    const dot = Math.max(-1, Math.min(1,
      directionA[0] * directionB[0]
      + directionA[1] * directionB[1]
      + directionA[2] * directionB[2],
    ));
    const angularErrorDeg = Math.acos(dot) * 180 / Math.PI;
    maxAngularErrorDeg = Math.max(maxAngularErrorDeg, angularErrorDeg);

    const projected = faceCoordsFromDirection(faceB, directionA);
    let reprojectionPx = 0;
    if (projected) {
      const expectedX = bx;
      const expectedY = by;
      const projectedX = wrapPixel((projected[0] + 1) * 0.5 * (size - 1), size);
      const projectedY = wrapPixel((projected[1] + 1) * 0.5 * (size - 1), size);
      reprojectionPx = Math.hypot(projectedX - expectedX, projectedY - expectedY);
    }
    maxReprojectionPx = Math.max(maxReprojectionPx, reprojectionPx);
    sumReprojectionPx += reprojectionPx;
    if (pixelMismatch) mismatchPixels += 1;
  }

  return {
    pair: pair.label,
    a: `${FACES[faceA].label} ${edgeA}`,
    b: `${FACES[faceB].label} ${edgeB}`,
    samples: size,
    maxChannelDelta,
    maxLumaDelta,
    meanAbsChannelDelta: absoluteChannelDelta / channels,
    rmsChannelDelta: Math.sqrt(sumSquaredChannelDelta / channels),
    maxReprojectionPx,
    meanReprojectionPx: sumReprojectionPx / size,
    maxAngularErrorDeg,
    mismatchPixels,
    exact: maxChannelDelta === 0 && mismatchPixels === 0,
  };
}

export function measureCubeSeams(faces, size) {
  const pairs = SEAM_PAIRS.map((pair) => measurePair(faces, size, pair));
  const maxChannelDelta = Math.max(...pairs.map((pair) => pair.maxChannelDelta));
  const maxLumaDelta = Math.max(...pairs.map((pair) => pair.maxLumaDelta));
  const mismatchPixels = pairs.reduce((sum, pair) => sum + pair.mismatchPixels, 0);
  const totalSamples = pairs.reduce((sum, pair) => sum + pair.samples, 0) * 3;
  return {
    faceSize: size,
    faceResolution: `${size} x ${size}`,
    faceCount: 6,
    seamCount: pairs.length,
    samplesPerSeam: size,
    totalComparisons: totalSamples,
    maxChannelDelta,
    maxLumaDelta,
    meanAbsChannelDelta: pairs.reduce((sum, pair) => sum + pair.meanAbsChannelDelta * pair.samples * 3, 0) / totalSamples,
    maxReprojectionPx: Math.max(...pairs.map((pair) => pair.maxReprojectionPx)),
    meanReprojectionPx: pairs.reduce((sum, pair) => sum + pair.meanReprojectionPx * pair.samples, 0) / (pairs.length * size),
    maxAngularErrorDeg: Math.max(...pairs.map((pair) => pair.maxAngularErrorDeg)),
    mismatchPixels,
    exactZero: maxChannelDelta === 0 && mismatchPixels === 0,
    pairs,
    faces: FACES.map((face) => ({
      id: face.id,
      label: face.label,
      name: face.name,
      width: size,
      height: size,
      pixels: size * size,
    })),
  };
}

export function rotationCadence(angularVelocityDegPerSec, updateHz) {
  const updates = 0;
  return {
    angularVelocityDegPerSec,
    updateHz,
    secondsPerUpdate: 1 / updateHz,
    degreesPerUpdate: angularVelocityDegPerSec / updateHz,
    updatesPerDegree: updateHz / angularVelocityDegPerSec,
    updates,
  };
}

export function simulateRotationCadence({
  angularVelocityDegPerSec = 30,
  refreshRates = [60, 90, 120],
  durationSeconds = 10,
} = {}) {
  return refreshRates.map((updateHz) => {
    const expectedUpdates = Math.round(durationSeconds * updateHz);
    let rotation = 0;
    let updates = 0;
    let maxStepError = 0;
    const expectedStep = angularVelocityDegPerSec / updateHz;
    for (let frame = 0; frame < expectedUpdates; frame += 1) {
      const step = expectedStep;
      rotation += step;
      updates += 1;
      maxStepError = Math.max(maxStepError, Math.abs(step - expectedStep));
    }
    return {
      updateHz,
      angularVelocityDegPerSec,
      degreesPerUpdate: expectedStep,
      expectedUpdates,
      actualUpdates: updates,
      droppedFrames: expectedUpdates - updates,
      totalRotationDeg: rotation,
      maxStepErrorDeg: maxStepError,
    };
  });
}
