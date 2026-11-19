'use strict';

const crypto = require('node:crypto');

function comparePcm(leftPcm, rightPcm) {
  const sampleCount = Math.min(leftPcm.length, rightPcm.length) / 2;
  let maxAbsoluteDifference = 0;
  let squaredDifferenceSum = 0;
  let differingSamples = 0;
  let firstDifference = null;

  for (let index = 0; index < sampleCount; index += 1) {
    const left = leftPcm.readInt16LE(index * 2);
    const right = rightPcm.readInt16LE(index * 2);
    const difference = Math.abs(left - right);
    if (difference > 0) {
      differingSamples += 1;
      if (!firstDifference) {
        firstDifference = { sampleIndex: index, left, right, difference };
      }
    }
    maxAbsoluteDifference = Math.max(maxAbsoluteDifference, difference);
    squaredDifferenceSum += difference * difference;
  }

  return {
    samplesCompared: sampleCount,
    differingSamples,
    maxAbsoluteDifference,
    rmsDifference:
      sampleCount > 0 ? Math.sqrt(squaredDifferenceSum / sampleCount) : 0,
    identical: maxAbsoluteDifference === 0 && leftPcm.length === rightPcm.length,
    leftBytes: leftPcm.length,
    rightBytes: rightPcm.length,
    firstDifference,
    sha256: {
      left: crypto.createHash('sha256').update(leftPcm).digest('hex'),
      right: crypto.createHash('sha256').update(rightPcm).digest('hex'),
    },
  };
}

function compareDsdBits(leftChannels, rightChannels) {
  if (leftChannels.length !== rightChannels.length) {
    return { identical: false, differingBits: null, reason: 'channel count differs' };
  }
  let differingBits = 0;
  for (let channel = 0; channel < leftChannels.length; channel += 1) {
    if (leftChannels[channel].length !== rightChannels[channel].length) {
      return { identical: false, differingBits: null, reason: 'bit count differs' };
    }
    for (let index = 0; index < leftChannels[channel].length; index += 1) {
      if (leftChannels[channel][index] !== rightChannels[channel][index]) differingBits += 1;
    }
  }
  return { identical: differingBits === 0, differingBits };
}

module.exports = { compareDsdBits, comparePcm };
