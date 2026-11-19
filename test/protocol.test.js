'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { encodeRtpPacket, parseRtpPacket } = require('../src/rtp/packet');
const { RtspMessageParser, parseRtpInfo, parseSdp } = require('../src/rtsp/protocol');
const { LOOPBACK_SDP } = require('../src/rtsp/sdp');
const { makeSendPlan } = require('../src/rtsp/server');

test('RTP packet encode/decode preserves the fixed header', () => {
  const payload = Buffer.from('media');
  const encoded = encodeRtpPacket({
    sequenceNumber: 65535,
    timestamp: 4294967290,
    ssrc: 0x11223344,
    payloadType: 96,
    marker: true,
    payload,
  });
  const decoded = parseRtpPacket(encoded);

  assert.equal(decoded.version, 2);
  assert.equal(decoded.sequenceNumber, 65535);
  assert.equal(decoded.timestamp, 4294967290);
  assert.equal(decoded.marker, true);
  assert.equal(decoded.payloadType, 96);
  assert.deepEqual(decoded.payload, payload);
});

test('RTSP parser handles pipelined messages and SDP tracks', () => {
  const parser = new RtspMessageParser();
  const first = 'RTSP/1.0 200 OK\r\nCSeq: 1\r\nContent-Length: 0\r\n\r\n';
  const second = 'RTSP/1.0 200 OK\r\nCSeq: 2\r\nContent-Length: 3\r\n\r\nabc';
  const messages = parser.feed(Buffer.from(first + second));

  assert.equal(messages.length, 2);
  assert.equal(messages[1].body, 'abc');
  const sdp = parseSdp(LOOPBACK_SDP);
  assert.equal(sdp.tracks.length, 2);
  assert.equal(sdp.tracks[0].rtpmap.clockRate, 90000);
  assert.equal(sdp.tracks[1].control, 'trackID=1');
});

test('RTP-Info parser and send plan preserve interleaving', () => {
  const info = parseRtpInfo(
    'url=rtsp://127.0.0.1/live/trackID=0;seq=2000;rtptime=225000,' +
    'url=rtsp://127.0.0.1/live/trackID=1;seq=1125;rtptime=120000',
  );
  assert.deepEqual(info.map((entry) => entry.seq), [2000, 1125]);

  const plan = makeSendPlan(8, 20);
  assert.deepEqual(plan.map((entry) => entry.index), [0, 2, 1, 4, 3, 6, 5, 7]);
  assert.equal(plan[1].atMs, 20);
  assert.equal(plan[2].atMs, 40);
});
