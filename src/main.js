'use strict';

const path = require('node:path');
const process = require('node:process');
const { runDsdComparison } = require('./audio/run-comparison');
const { buildAssertions, writeReports } = require('./report');
const { runLoopbackScenario } = require('./scenario');

function printSummary(result) {
  const aggregate = result.aggregate.all;
  const reorder = aggregate.reorder.reorderedPackets;
  const jitter = aggregate.jitter.transitDeltaAbs;

  console.log('\nRTSP loopback');
  console.log(`  DESCRIBE : ${result.rtsp.describe.statusCode} (${result.rtsp.describe.trackCount} tracks)`);
  for (const setup of result.rtsp.setups) {
    console.log(`  SETUP    : ${setup.control} ${setup.statusCode}, client_port=${setup.clientPort}, server_port=${setup.serverPort}`);
  }
  console.log(`  PLAY     : ${result.rtsp.initialPlay.statusCode}, RTP-Info=${JSON.stringify(result.rtsp.initialPlay.rtpInfo)}`);
  console.log(`  SEEK     : ${result.rtsp.seekPlay.statusCode}, Range=${result.rtsp.seekPlay.requestRange}`);
  console.log(`  SESSION  : ${result.rtsp.sessionAfterSeek.state}, plays=${result.rtsp.sessionAfterSeek.playCount}, seeks=${result.rtsp.sessionAfterSeek.seekCount}, sameId=${result.rtsp.sessionIdStable}`);

  console.log('\nRTP reorder and jitter (post-seek per track)');
  console.table(Object.fromEntries(Object.entries(result.streams).map(([name, stream]) => [
    name,
    {
      packets: stream.post.packetCount,
      reordered: stream.post.reorder.reorderedPackets.count,
      'reorder p50 ms': stream.post.reorder.reorderedPackets.p50,
      'reorder p95 ms': stream.post.reorder.reorderedPackets.p95,
      'jitter p50 ms': stream.post.jitter.transitDeltaAbs.p50,
      'jitter p95 ms': stream.post.jitter.transitDeltaAbs.p95,
      'RFC3550 ms': Number(stream.post.jitter.finalRfc3550JitterMs.toFixed(3)),
    },
  ])));

  console.log('Combined reorder-delay histogram');
  console.table(reorder.histogram);
  console.log('Combined |transit delta| jitter histogram');
  console.table(jitter.histogram);
  console.log(`Combined reordered packets: count=${reorder.count}, p50=${reorder.p50} ms, p95=${reorder.p95} ms, max=${reorder.max} ms`);

  console.log('\nSeek RTP timestamp alignment');
  console.table(Object.fromEntries(Object.entries(result.streams).map(([name, stream]) => {
    const before = stream.alignment.beforeSeek;
    const after = stream.alignment.afterSeek;
    const continuity = stream.alignment.continuityAcrossSeek;
    return [name, {
      'before seq/ts': `${before.rtpInfoSequence}/${before.rtpInfoTimestamp}`,
      'after seq/ts': `${after.rtpInfoSequence}/${after.rtpInfoTimestamp}`,
      'after NPT ms': Number(after.firstPacketNptMs.toFixed(3)),
      'ts jump': continuity.timestampJumpSamples,
      'expected jump': continuity.expectedTimestampJumpSamples,
      resynced: stream.timeline.resynchronized,
    }];
  })));

  console.log('\nDFF vs DSF decoded PCM');
  console.log(`  DSD bits : identical=${result.dsd.bitComparison.identical}, differing=${result.dsd.bitComparison.differingBits}`);
  console.log(`  PCM      : samples=${result.dsd.pcmComparison.samplesCompared}, differing=${result.dsd.pcmComparison.differingSamples}, maxDiff=${result.dsd.pcmComparison.maxAbsoluteDifference}, rmsDiff=${result.dsd.pcmComparison.rmsDifference}`);
  console.log(`  PCM hash : ${result.dsd.pcmComparison.sha256.left}`);
}

async function main() {
  const artifactDirectory = path.resolve(__dirname, '..', 'artifacts');
  const mediaDirectory = path.join(artifactDirectory, 'media');

  const result = await runLoopbackScenario();
  const transcript = result.rtsp.transcript;
  delete result.rtsp.transcript;
  result.dsd = await runDsdComparison(mediaDirectory);
  const assertions = buildAssertions(result);
  result.assertions = assertions.assertions;
  result.passed = assertions.passed;

  const reportPaths = await writeReports(result, artifactDirectory, transcript);
  printSummary(result);

  console.log(`\nResult: ${result.passed ? 'PASS' : 'FAIL'}`);
  console.log(`Report: ${reportPaths.reportMarkdownPath}`);
  console.log(`JSON  : ${reportPaths.reportJsonPath}`);
  if (!result.passed) process.exitCode = 1;
}

if (require.main === module) {
  main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
}

module.exports = { main, printSummary };
