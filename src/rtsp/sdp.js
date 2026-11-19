'use strict';

const TRACKS = [
  {
    control: 'trackID=0',
    kind: 'video',
    media: 'video',
    payloadType: 96,
    encoding: 'H264',
    clockRate: 90000,
    channels: 0,
    ssrc: 0x11223344,
    sequenceBase: 2000,
    timestampBase: 0,
    samplesPerPacket: 1800,
  },
  {
    control: 'trackID=1',
    kind: 'audio',
    media: 'audio',
    payloadType: 97,
    encoding: 'MPEG4-GENERIC',
    clockRate: 48000,
    channels: 2,
    ssrc: 0x55667788,
    sequenceBase: 1000,
    timestampBase: 0,
    samplesPerPacket: 960,
  },
];

const LOOPBACK_SDP = [
  'v=0',
  'o=- 0 0 IN IP4 127.0.0.1',
  's=RTSP RTP Loopback Verification',
  'c=IN IP4 127.0.0.1',
  't=0 0',
  'a=control:*',
  'm=video 0 RTP/AVP 96',
  'a=rtpmap:96 H264/90000',
  'a=fmtp:96 packetization-mode=1;profile-level-id=42e01f',
  'a=control:trackID=0',
  'm=audio 0 RTP/AVP 97',
  'a=rtpmap:97 MPEG4-GENERIC/48000/2',
  'a=fmtp:97 profile-level-id=1;mode=AAC-hbr;sizelength=13;indexlength=3;indexdeltalength=3;config=1190',
  'a=control:trackID=1',
  '',
].join('\r\n');

function trackForControl(control) {
  return TRACKS.find((track) => track.control === control) || null;
}

module.exports = { LOOPBACK_SDP, TRACKS, trackForControl };
