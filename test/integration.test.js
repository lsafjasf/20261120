'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { runLoopbackScenario, SEEK_NPT_SECONDS } = require('../src/scenario');

test('RTSP DESCRIBE/SETUP/PLAY, seek, and RTP resynchronization', { timeout: 10000 }, async () => {
  const result = await runLoopbackScenario({ packetCount: 10, timeoutMs: 5000 });

  assert.equal(result.rtsp.describe.statusCode, 200);
  assert.equal(result.rtsp.describe.trackCount, 2);
  assert.equal(result.rtsp.setups.length, 2);
  assert.ok(result.rtsp.setups.every((setup) => setup.statusCode === 200));
  assert.equal(result.rtsp.initialPlay.statusCode, 200);
  assert.equal(result.rtsp.initialPlay.rtpInfo.length, 2);
  assert.equal(result.rtsp.seekPlay.statusCode, 200);
  assert.equal(result.rtsp.sessionIdStable, true);
  assert.equal(result.rtsp.seekStateCorrect, true);
  assert.equal(result.rtsp.sessionAfterSeek.state, 'PLAYING');
  assert.equal(result.rtsp.sessionAfterSeek.playCount, 2);
  assert.equal(result.rtsp.sessionAfterSeek.seekCount, 1);
  assert.equal(result.rtsp.sessionAfterSeek.lastRange.startNpt, SEEK_NPT_SECONDS);
  assert.equal(result.rtsp.sessionAfterTeardown.state, 'TEARDOWN');

  for (const stream of Object.values(result.streams)) {
    assert.equal(stream.pre.packetCount >= 8, true);
    assert.equal(stream.post.packetCount, 10);
    assert.equal(stream.pre.contiguous, true);
    assert.equal(stream.post.contiguous, true);
    assert.equal(stream.timeline.pre.contiguousTimestamps, true);
    assert.equal(stream.timeline.post.contiguousTimestamps, true);
    assert.equal(stream.timeline.resynchronized, true);
    assert.equal(stream.alignment.afterSeek.sequenceAligned, true);
    assert.equal(stream.alignment.afterSeek.timestampAligned, true);
    assert.equal(stream.alignment.afterSeek.timestampDeltaFromRequestedNptMs, 0);
    assert.equal(stream.alignment.continuityAcrossSeek.timelineJumpMatchesSeek, true);
    assert.ok(stream.post.reorder.reorderedPackets.count > 0);
    assert.ok(stream.post.jitter.transitDeltaAbs.count > 0);
  }

  assert.ok(result.aggregate.all.reorder.reorderedPackets.count > 0);
  assert.ok(result.aggregate.all.jitter.transitDeltaAbs.max > 0);
});
