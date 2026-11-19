'use strict';

const fs = require('node:fs/promises');
const path = require('node:path');
const { compareDsdBits, comparePcm } = require('./compare');
const { decodeDsdToPcm, generateFixture } = require('./dsd-core');
const { encodeDff, parseDff } = require('./dff');
const { encodeDsf, parseDsf } = require('./dsf');

async function runDsdComparison(outputDirectory) {
  await fs.mkdir(outputDirectory, { recursive: true });
  const fixture = generateFixture();
  const dffBuffer = encodeDff({
    channels: fixture.channels,
    sampleRate: fixture.dsdRate,
  });
  const dsfBuffer = encodeDsf({
    channels: fixture.channels,
    sampleRate: fixture.dsdRate,
  });

  const dffPath = path.join(outputDirectory, 'same-content.dff');
  const dsfPath = path.join(outputDirectory, 'same-content.dsf');
  await fs.writeFile(dffPath, dffBuffer);
  await fs.writeFile(dsfPath, dsfBuffer);

  const dff = parseDff(dffBuffer);
  const dsf = parseDsf(dsfBuffer);
  const bitComparison = compareDsdBits(dff.channels, dsf.channels);
  const decodedDff = decodeDsdToPcm(dff.channels);
  const decodedDsf = decodeDsdToPcm(dsf.channels);
  const pcmComparison = comparePcm(decodedDff.pcm, decodedDsf.pcm);

  const dffPcmPath = path.join(outputDirectory, 'decoded-dff.pcm');
  const dsfPcmPath = path.join(outputDirectory, 'decoded-dsf.pcm');
  await fs.writeFile(dffPcmPath, decodedDff.pcm);
  await fs.writeFile(dsfPcmPath, decodedDsf.pcm);

  return {
    content: {
      source: 'deterministic stereo DSD fixture',
      pcmRate: fixture.pcmRate,
      dsdRate: fixture.dsdRate,
      decimation: fixture.decimation,
      channelCount: fixture.channels.length,
      samplesPerChannel: decodedDff.sampleCount,
    },
    files: {
      dff: {
        path: dffPath,
        bytes: dffBuffer.length,
        container: dff.container,
        sampleRate: dff.sampleRate,
        channelCount: dff.channelCount,
        bitCountPerChannel: dff.bitCount,
        layout: 'channel-interleaved DSD bytes',
        bitOrder: 'MSB-first',
      },
      dsf: {
        path: dsfPath,
        bytes: dsfBuffer.length,
        container: dsf.container,
        sampleRate: dsf.sampleRate,
        channelCount: dsf.channelCount,
        bitCountPerChannel: dsf.bitCount,
        blockSize: dsf.blockSize,
        layout: `${dsf.blockSize}-byte blocks, channel-major per block`,
        bitOrder: 'LSB-first',
      },
      pcm: {
        dff: {
          path: dffPcmPath,
          bytes: decodedDff.pcm.length,
          format: 'signed 16-bit little-endian, interleaved',
        },
        dsf: {
          path: dsfPcmPath,
          bytes: decodedDsf.pcm.length,
          format: 'signed 16-bit little-endian, interleaved',
        },
      },
    },
    headerDifference: {
      byteSizeDelta: dffBuffer.length - dsfBuffer.length,
      sameSampleRate: dff.sampleRate === dsf.sampleRate,
      sameChannelCount: dff.channelCount === dsf.channelCount,
      sameBitsPerChannel: dff.bitCount === dsf.bitCount,
      formatSpecificHeaders: [
        'DFF: FRM8/DSD form, FVER, PROP(SND/FS/CHNL/CMPR), DSD chunk, MSB-first',
        'DSF: DSD header, fmt chunk, data chunk, 4096-byte blocks, LSB-first',
      ],
    },
    bitComparison,
    pcmComparison,
  };
}

module.exports = { runDsdComparison };
