'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { compareDsdBits, comparePcm } = require('../src/audio/compare');
const { decodeDsdToPcm, generateFixture } = require('../src/audio/dsd-core');
const { encodeDff, parseDff } = require('../src/audio/dff');
const { encodeDsf, parseDsf } = require('../src/audio/dsf');

test('DFF and DSF round-trip the same DSD content and decode to identical PCM', () => {
  const fixture = generateFixture();
  const dffBuffer = encodeDff({ channels: fixture.channels, sampleRate: fixture.dsdRate });
  const dsfBuffer = encodeDsf({ channels: fixture.channels, sampleRate: fixture.dsdRate });
  const dff = parseDff(dffBuffer);
  const dsf = parseDsf(dsfBuffer);

  assert.notEqual(dffBuffer.length, dsfBuffer.length);
  assert.equal(dff.sampleRate, 2822400);
  assert.equal(dsf.sampleRate, 2822400);
  assert.equal(dff.channelCount, 2);
  assert.equal(dsf.channelCount, 2);
  assert.equal(dff.bitCount, 262144);
  assert.equal(dsf.bitCount, 262144);
  assert.deepEqual(compareDsdBits(dff.channels, dsf.channels), {
    identical: true,
    differingBits: 0,
  });

  const decodedDff = decodeDsdToPcm(dff.channels);
  const decodedDsf = decodeDsdToPcm(dsf.channels);
  const comparison = comparePcm(decodedDff.pcm, decodedDsf.pcm);
  assert.equal(comparison.samplesCompared, 8192);
  assert.equal(comparison.differingSamples, 0);
  assert.equal(comparison.maxAbsoluteDifference, 0);
  assert.equal(comparison.rmsDifference, 0);
  assert.equal(comparison.identical, true);
  assert.equal(comparison.sha256.left, comparison.sha256.right);
});
