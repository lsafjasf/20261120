'use strict';

const { performance } = require('node:perf_hooks');
const { RtspClient } = require('./rtsp/client');
const { RtspLoopbackServer } = require('./rtsp/server');
const { TRACKS } = require('./rtsp/sdp');
const { UdpRtpReceiver } = require('./rtp/receiver');
const { RtpResequencer, JITTER_BINS, REORDER_BINS } = require('./rtp/resequencer');
const { mod, summarize, waitFor } = require('./utils');

const PRE_ORDERED_PACKETS = 8;
const SEEK_NPT_SECONDS = 2.5;

function signed32(value) {
  return ((value + 0x80000000) >>> 0) - 0x80000000;
}

function signed16(value) {
  return ((value + 0x8000) & 0xffff) - 0x8000;
}

function parseServerPort(transport) {
  const match = /server_port=(\d+)(?:-(\d+))?/i.exec(transport || '');
  return match ? Number.parseInt(match[1], 10) : null;
}

function infoForControl(rtpInfo, control) {
  return rtpInfo.find((entry) => entry.url.endsWith(`/${control}`)) || null;
}

function packetStats(snapshots) {
  const packets = snapshots.flatMap((snapshot) => snapshot.packets);
  const reorderDelays = packets
    .map((packet) => packet.reorderDelayMs)
    .filter((value) => value !== null);
  const reorderedDelays = reorderDelays.filter((value) => value > 0.1);
  const jitterDeltas = packets
    .map((packet) => packet.transitDeltaMs)
    .filter((value) => value !== null)
    .map(Math.abs);
  const smoothedJitter = packets
    .map((packet) => packet.smoothedJitterMs)
    .filter((value) => value !== null);

  return {
    packetCount: packets.length,
    reorder: {
      allPackets: summarize(reorderDelays, { bins: REORDER_BINS }),
      reorderedPackets: summarize(reorderedDelays, { bins: REORDER_BINS }),
    },
    jitter: {
      transitDeltaAbs: summarize(jitterDeltas, { bins: JITTER_BINS }),
      maxRfc3550SmoothedJitterMs:
        smoothedJitter.length > 0 ? Math.max(...smoothedJitter) : 0,
    },
  };
}

function checkTimeline(snapshot, definition) {
  const packets = snapshot.packets;
  if (packets.length === 0) {
    return { contiguousSequence: false, contiguousTimestamps: false, firstMatchesRtpInfo: false };
  }
  let contiguousSequence = true;
  let contiguousTimestamps = true;
  for (let index = 1; index < packets.length; index += 1) {
    if (
      packets[index].sequenceNumber !==
      mod(packets[index - 1].sequenceNumber + 1, 65536)
    ) {
      contiguousSequence = false;
    }
    if (
      packets[index].timestamp !==
      mod(packets[index - 1].timestamp + definition.samplesPerPacket, 2 ** 32)
    ) {
      contiguousTimestamps = false;
    }
  }
  return {
    contiguousSequence,
    contiguousTimestamps,
    firstMatchesRtpInfo:
      packets[0].sequenceNumber === snapshot.meta.sequenceNumber &&
      packets[0].timestamp === snapshot.meta.timestamp,
  };
}

function summarizeSession(server, sessionId, setupByControl) {
  const session = server.sessions.get(sessionId);
  if (!session) return null;
  const tracks = {};
  for (const [control, configured] of session.tracks) {
    tracks[control] = {
      clientPort: configured.clientPort,
      serverPort: configured.serverPort,
      ssrc: configured.definition.ssrc,
      setupTransportUnchanged:
        setupByControl[control]?.clientPort === configured.clientPort &&
        setupByControl[control]?.serverPort === configured.serverPort,
    };
  }
  return {
    id: session.id,
    state: session.state,
    playCount: session.playCount,
    seekCount: session.seekCount,
    generation: session.generation,
    lastRange: session.lastRange,
    tracks,
  };
}

async function runLoopbackScenario({ packetCount = 16, timeoutMs = 5000 } = {}) {
  const startedAt = performance.now();
  const server = await new RtspLoopbackServer({ packetCount }).start();
  const client = new RtspClient(`${server.baseUri}/`, { timeoutMs });
  const receivers = [];

  try {
    await client.connect();
    const describeResponse = await client.describe();
    const setupRecords = [];
    const setupByControl = {};
    const receiverByControl = new Map();

    for (const sdpTrack of client.sdpTracks) {
      const definition = TRACKS.find((track) => track.control === sdpTrack.control);
      if (!definition) continue;
      const receiver = new UdpRtpReceiver({ name: definition.kind });
      const port = await receiver.bind();
      const state = {
        control: sdpTrack.control,
        definition,
        receiver,
        resequencer: null,
      };
      receiver.onPacket((packet, receivedAt) => {
        if (state.resequencer) state.resequencer.push(packet, receivedAt);
      });
      receivers.push(receiver);
      receiverByControl.set(sdpTrack.control, state);

      const setupResponse = await client.setup(sdpTrack.control, port);
      const record = {
        control: sdpTrack.control,
        media: sdpTrack.media,
        statusCode: setupResponse.statusCode,
        clientPort: port,
        serverPort: parseServerPort(setupResponse.headers.transport),
        ssrc: setupResponse.headers.transport?.match(/ssrc=([0-9a-f]+)/i)?.[1] || null,
        transport: setupResponse.headers.transport,
        session: setupResponse.headers.session,
      };
      setupRecords.push(record);
      setupByControl[sdpTrack.control] = record;
    }

    const initialPlay = await client.play();
    for (const [control, state] of receiverByControl) {
      const info = infoForControl(initialPlay.rtpInfo, control);
      if (!info) throw new Error(`Missing initial RTP-Info for ${control}`);
      state.resequencer = new RtpResequencer({
        name: state.definition.kind,
        clockRate: state.definition.clockRate,
        sequenceNumber: info.seq,
        timestamp: info.rtptime,
        generation: 1,
        meta: {
          nptSeconds: 0,
          seek: false,
          rtpInfo: info,
          requestRange: null,
        },
      });
    }

    await waitFor(
      () =>
        [...receiverByControl.values()].every(
          (state) => state.resequencer.packetsFor(1).length >= PRE_ORDERED_PACKETS,
        ),
      { timeoutMs, label: 'pre-seek RTP packets' },
    );
    await new Promise((resolve) => setTimeout(resolve, 30));

    const preSnapshots = {};
    for (const [control, state] of receiverByControl) {
      preSnapshots[control] = state.resequencer.snapshot(1);
    }

    const seekPlay = await client.play({ range: `npt=${SEEK_NPT_SECONDS.toFixed(3)}-` });
    for (const [control, state] of receiverByControl) {
      const info = infoForControl(seekPlay.rtpInfo, control);
      if (!info) throw new Error(`Missing post-seek RTP-Info for ${control}`);
      state.resequencer.rebase({
        sequenceNumber: info.seq,
        timestamp: info.rtptime,
        generation: 2,
        nptSeconds: SEEK_NPT_SECONDS,
        seek: true,
        rtpInfo: info,
        requestRange: seekPlay.response.headers.range,
      });
    }

    await waitFor(
      () =>
        [...receiverByControl.values()].every(
          (state) => state.resequencer.packetsFor(2).length >= packetCount,
        ),
      { timeoutMs, label: 'post-seek RTP packets' },
    );

    const postSnapshots = {};
    for (const [control, state] of receiverByControl) {
      postSnapshots[control] = state.resequencer.snapshot(2);
    }
    const sessionAfterSeek = summarizeSession(server, client.sessionId, setupByControl);

    const streams = {};
    for (const [control, state] of receiverByControl) {
      const definition = state.definition;
      const pre = preSnapshots[control];
      const post = postSnapshots[control];
      const initialInfo = infoForControl(initialPlay.rtpInfo, control);
      const seekInfo = infoForControl(seekPlay.rtpInfo, control);
      const expectedSeekTimestamp = mod(
        definition.timestampBase + Math.round(SEEK_NPT_SECONDS * definition.clockRate),
        2 ** 32,
      );
      const expectedSeekSequence = mod(
        definition.sequenceBase + Math.round((SEEK_NPT_SECONDS * 1000) / 20),
        65536,
      );
      const preTimeline = checkTimeline(pre, definition);
      const postTimeline = checkTimeline(post, definition);
      const firstPost = post.first;
      const lastPre = pre.last;

      streams[definition.kind] = {
        control,
        clockRate: definition.clockRate,
        samplesPerPacket: definition.samplesPerPacket,
        pre,
        post,
        alignment: {
          beforeSeek: {
            requestedNptMs: 0,
            expectedSequence: mod(definition.sequenceBase, 65536),
            expectedRtpTimestamp: mod(definition.timestampBase, 2 ** 32),
            rtpInfoSequence: initialInfo?.seq ?? null,
            rtpInfoTimestamp: initialInfo?.rtptime ?? null,
            firstPacket: pre.first,
            firstPacketNptMs:
              pre.first ? (pre.first.timestamp / definition.clockRate) * 1000 : null,
            sequenceAligned: pre.first?.sequenceNumber === initialInfo?.seq,
            timestampAligned: pre.first?.timestamp === initialInfo?.rtptime,
          },
          afterSeek: {
            requestedNptMs: SEEK_NPT_SECONDS * 1000,
            expectedSequence: expectedSeekSequence,
            expectedRtpTimestamp: expectedSeekTimestamp,
            rtpInfoSequence: seekInfo?.seq ?? null,
            rtpInfoTimestamp: seekInfo?.rtptime ?? null,
            firstPacket: firstPost,
            firstPacketNptMs: firstPost
              ? (firstPost.timestamp / definition.clockRate) * 1000
              : null,
            timestampDeltaFromRequestedNptMs: firstPost
              ? ((signed32(firstPost.timestamp - expectedSeekTimestamp) /
                  definition.clockRate) *
                1000)
              : null,
            sequenceAligned:
              firstPost?.sequenceNumber === seekInfo?.seq &&
              seekInfo?.seq === expectedSeekSequence,
            timestampAligned:
              firstPost?.timestamp === seekInfo?.rtptime &&
              seekInfo?.rtptime === expectedSeekTimestamp,
          },
          continuityAcrossSeek: {
            previousLastSequence: lastPre?.sequenceNumber ?? null,
            resumedFirstSequence: firstPost?.sequenceNumber ?? null,
            sequenceJump: lastPre && firstPost
              ? signed16(firstPost.sequenceNumber - lastPre.sequenceNumber)
              : null,
            previousLastTimestamp: lastPre?.timestamp ?? null,
            resumedFirstTimestamp: firstPost?.timestamp ?? null,
            timestampJumpSamples: lastPre && firstPost
              ? signed32(firstPost.timestamp - lastPre.timestamp)
              : null,
            expectedTimestampJumpSamples: lastPre && firstPost
              ? signed32(expectedSeekTimestamp - lastPre.timestamp)
              : null,
            timelineJumpMatchesSeek:
              lastPre && firstPost
                ? signed32(firstPost.timestamp - lastPre.timestamp) ===
                  signed32(expectedSeekTimestamp - lastPre.timestamp)
                : false,
          },
        },
        timeline: {
          pre: preTimeline,
          post: postTimeline,
          resynchronized:
            Boolean(firstPost) &&
            postTimeline.contiguousSequence &&
            postTimeline.contiguousTimestamps &&
            postTimeline.firstMatchesRtpInfo,
        },
      };
    }

    const sessionIdStable = client.sessionId === sessionAfterSeek.id;
    const seekStateCorrect =
      sessionAfterSeek.state === 'PLAYING' &&
      sessionAfterSeek.playCount === 2 &&
      sessionAfterSeek.seekCount === 1 &&
      sessionAfterSeek.lastRange.startNpt === SEEK_NPT_SECONDS &&
      sessionAfterSeek.tracks['trackID=0'].setupTransportUnchanged &&
      sessionAfterSeek.tracks['trackID=1'].setupTransportUnchanged;

    await client.teardown();
    const sessionAfterTeardown = summarizeSession(server, client.sessionId, setupByControl);
    const durationMs = performance.now() - startedAt;

    return {
      startedAt: new Date().toISOString(),
      durationMs: Number(durationMs.toFixed(3)),
      server: { baseUri: server.baseUri },
      rtsp: {
        describe: {
          statusCode: describeResponse.statusCode,
          contentType: describeResponse.headers['content-type'],
          trackCount: client.sdpTracks.length,
          tracks: client.sdpTracks.map((track) => ({
            media: track.media,
            control: track.control,
            encoding: track.rtpmap?.encoding,
            clockRate: track.rtpmap?.clockRate,
          })),
        },
        setups: setupRecords,
        initialPlay: {
          statusCode: initialPlay.response.statusCode,
          session: initialPlay.response.headers.session,
          range: initialPlay.response.headers.range,
          rtpInfo: initialPlay.rtpInfo,
        },
        seekPlay: {
          statusCode: seekPlay.response.statusCode,
          requestRange: seekPlay.response.headers.range,
          session: seekPlay.response.headers.session,
          rtpInfo: seekPlay.rtpInfo,
        },
        sessionAfterSeek,
        sessionAfterTeardown,
        sessionIdStable,
        seekStateCorrect,
        transcript: client.transcript,
      },
      streams,
      aggregate: {
        beforeSeek: packetStats(Object.values(preSnapshots)),
        afterSeek: packetStats(Object.values(postSnapshots)),
        all: packetStats([...Object.values(preSnapshots), ...Object.values(postSnapshots)]),
      },
    };
  } finally {
    for (const receiver of receivers) receiver.close();
    client.close();
    await server.stop();
  }
}

module.exports = {
  PRE_ORDERED_PACKETS,
  SEEK_NPT_SECONDS,
  packetStats,
  runLoopbackScenario,
};
