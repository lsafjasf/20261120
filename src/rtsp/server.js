'use strict';

const net = require('node:net');
const { randomUUID } = require('node:crypto');
const { encodeRtpPacket } = require('../rtp/packet');
const { bindUdpPair, closeSocket } = require('../rtp/udp');
const { mod } = require('../utils');
const {
  RtspMessageParser,
  formatRtpInfo,
  formatRtspResponse,
  parseRequest,
} = require('./protocol');
const { LOOPBACK_SDP, TRACKS, trackForControl } = require('./sdp');

function makeSendPlan(count, intervalMs) {
  const plan = [{ index: 0, atMs: 0 }];
  let position = 1;
  for (let odd = 1; odd < count; odd += 2) {
    if (odd + 1 < count) {
      plan.push({ index: odd + 1, atMs: position * intervalMs });
      position += 1;
    }
    plan.push({ index: odd, atMs: position * intervalMs });
    position += 1;
  }
  return plan;
}

function parseClientPort(transport) {
  const match = /client_port=(\d+)(?:-(\d+))?/i.exec(transport || '');
  if (!match) return null;
  return {
    rtp: Number.parseInt(match[1], 10),
    rtcp: match[2] ? Number.parseInt(match[2], 10) : Number.parseInt(match[1], 10) + 1,
  };
}

function parseNpt(rangeHeader) {
  const match = /(?:^|;)\s*npt=([\d.]+)-/i.exec(rangeHeader || '');
  if (!match) return null;
  const value = Number.parseFloat(match[1]);
  return Number.isFinite(value) && value >= 0 ? value : null;
}

function extractControl(uri) {
  const path = new URL(uri).pathname;
  const segment = path.split('/').filter(Boolean).pop();
  return segment || '';
}

class RtspLoopbackServer {
  constructor({ host = '127.0.0.1', port = 0, packetCount = 16, packetIntervalMs = 20 } = {}) {
    this.host = host;
    this.requestedPort = port;
    this.packetCount = packetCount;
    this.packetIntervalMs = packetIntervalMs;
    this.sessions = new Map();
    this.connections = new Set();
    this.server = null;
    this.port = null;
  }

  get baseUri() {
    return `rtsp://${this.host}:${this.port}/live`;
  }

  async start() {
    this.server = net.createServer((socket) => this.handleConnection(socket));
    await new Promise((resolve, reject) => {
      this.server.once('error', reject);
      this.server.listen(this.requestedPort, this.host, resolve);
    });
    this.port = this.server.address().port;
    return this;
  }

  async stop() {
    for (const session of this.sessions.values()) {
      this.cancelStreaming(session);
      for (const track of session.tracks.values()) {
        closeSocket(track.udp);
        closeSocket(track.rtcpUdp);
      }
    }
    for (const connection of this.connections) connection.destroy();
    if (this.server) {
      await new Promise((resolve) => this.server.close(resolve));
    }
  }

  handleConnection(socket) {
    this.connections.add(socket);
    const parser = new RtspMessageParser();
    let queue = Promise.resolve();

    socket.on('close', () => this.connections.delete(socket));
    socket.on('error', () => this.connections.delete(socket));
    socket.on('data', (chunk) => {
      for (const parsed of parser.feed(chunk)) {
        const request = parseRequest(parsed);
        queue = queue
          .then(() => this.handleRequest(socket, request))
          .catch((error) => this.sendError(socket, request, error));
      }
    });
  }

  baseHeaders(request) {
    return {
      CSeq: request.headers.cseq || '0',
      Date: new Date().toUTCString(),
      Server: 'Node-RTSP-Loopback/1.0',
    };
  }

  send(socket, statusCode, statusMessage, request, headers = {}, body = '') {
    const responseHeaders = {
      ...this.baseHeaders(request),
      ...headers,
    };
    socket.write(formatRtspResponse(statusCode, statusMessage, responseHeaders, body));
  }

  sendError(socket, request, error) {
    if (!socket.destroyed) {
      this.send(socket, 500, 'Internal Server Error', request, {
        'X-Error': String(error.message || error).replace(/[\r\n]+/g, ' '),
      });
    }
  }

  async handleRequest(socket, request) {
    switch (request.method) {
      case 'OPTIONS':
        return this.send(socket, 200, 'OK', request, {
          Public: 'OPTIONS, DESCRIBE, SETUP, PLAY, TEARDOWN',
        });
      case 'DESCRIBE':
        return this.send(socket, 200, 'OK', request, {
          'Content-Type': 'application/sdp',
          'Content-Base': `${this.baseUri}/`,
        }, LOOPBACK_SDP);
      case 'SETUP':
        return this.handleSetup(socket, request);
      case 'PLAY':
        return this.handlePlay(socket, request);
      case 'TEARDOWN':
        return this.handleTeardown(socket, request);
      default:
        return this.send(socket, 405, 'Method Not Allowed', request, {
          Allow: 'OPTIONS, DESCRIBE, SETUP, PLAY, TEARDOWN',
        });
    }
  }

  sessionFromRequest(request) {
    const id = (request.headers.session || '').split(';')[0].trim();
    return id ? this.sessions.get(id) || null : null;
  }

  async handleSetup(socket, request) {
    const control = extractControl(request.uri);
    const trackDefinition = trackForControl(control);
    const ports = parseClientPort(request.headers.transport);
    if (!trackDefinition || !ports) {
      return this.send(socket, 400, 'Bad Request', request);
    }

    let session = this.sessionFromRequest(request);
    if (!session) {
      session = {
        id: randomUUID().replace(/-/g, '').slice(0, 16).toUpperCase(),
        state: 'READY',
        tracks: new Map(),
        timers: new Set(),
        generation: 0,
        playCount: 0,
        seekCount: 0,
        lastRange: null,
        clientHost: this.host,
      };
      this.sessions.set(session.id, session);
    }
    if (session.state !== 'READY') {
      return this.send(socket, 455, 'Method Not Valid in This State', request, {
        Session: session.id,
      });
    }

    const oldTrack = session.tracks.get(control);
    if (oldTrack) {
      closeSocket(oldTrack.udp);
      closeSocket(oldTrack.rtcpUdp);
      session.tracks.delete(control);
    }

    const pair = await bindUdpPair(this.host);
    const serverPort = pair.rtpPort;
    session.tracks.set(control, {
      definition: trackDefinition,
      clientPort: ports.rtp,
      clientRtcpPort: ports.rtcp,
      serverPort,
      udp: pair.rtp,
      rtcpUdp: pair.rtcp,
    });

    const transport = [
      'RTP/AVP',
      'unicast',
      `client_port=${ports.rtp}-${ports.rtcp}`,
      `server_port=${serverPort}-${serverPort + 1}`,
      `ssrc=${trackDefinition.ssrc.toString(16).padStart(8, '0')}`,
    ].join(';');

    return this.send(socket, 200, 'OK', request, {
      Transport: transport,
      Session: `${session.id};timeout=60`,
    });
  }

  cancelStreaming(session) {
    session.generation += 1;
    for (const timer of session.timers) clearTimeout(timer);
    session.timers.clear();
  }

  handlePlay(socket, request) {
    const session = this.sessionFromRequest(request);
    if (!session) {
      return this.send(socket, 454, 'Session Not Found', request);
    }
    if (session.state === 'TEARDOWN') {
      return this.send(socket, 455, 'Method Not Valid in This State', request, {
        Session: session.id,
      });
    }
    if (session.tracks.size === 0) {
      return this.send(socket, 455, 'Method Not Valid in This State', request, {
        Session: session.id,
      });
    }

    const requestedNpt = parseNpt(request.headers.range);
    if (request.headers.range && requestedNpt === null) {
      return this.send(socket, 400, 'Bad Request', request, { Session: session.id });
    }

    const isSeek = session.playCount > 0 && request.headers.range !== undefined;
    const startNpt = requestedNpt ?? 0;
    this.cancelStreaming(session);
    const generation = session.generation;
    const packetIndex = Math.round((startNpt * 1000) / this.packetIntervalMs);
    const rtpInfoEntries = [];
    const playbackState = new Map();

    for (const [control, configured] of session.tracks) {
      const definition = configured.definition;
      const sequenceNumber = mod(definition.sequenceBase + packetIndex, 65536);
      const timestamp = mod(
        definition.timestampBase + Math.round(startNpt * definition.clockRate),
        2 ** 32,
      );
      playbackState.set(control, { sequenceNumber, timestamp });
      rtpInfoEntries.push({
        url: `${this.baseUri}/${control}`,
        seq: sequenceNumber,
        rtptime: timestamp,
      });
    }

    session.state = 'PLAYING';
    session.playCount += 1;
    if (isSeek) session.seekCount += 1;
    session.lastRange = {
      startNpt,
      raw: request.headers.range || null,
      isSeek,
    };
    session.activeGeneration = generation;

    const responseHeaders = {
      Session: `${session.id};timeout=60`,
      'RTP-Info': formatRtpInfo(rtpInfoEntries),
      Range: `npt=${startNpt.toFixed(3)}-`,
    };
    this.send(socket, 200, 'OK', request, responseHeaders);

    setTimeout(() => {
      if (session.activeGeneration === generation && session.state === 'PLAYING') {
        this.startStreaming(session, generation, playbackState);
      }
    }, 0);

    return undefined;
  }

  startStreaming(session, generation, playbackState) {
    const plan = makeSendPlan(this.packetCount, this.packetIntervalMs);
    for (const item of plan) {
      const timer = setTimeout(() => {
        session.timers.delete(timer);
        if (session.activeGeneration !== generation || session.state !== 'PLAYING') return;

        for (const [control, configured] of session.tracks) {
          const definition = configured.definition;
          const start = playbackState.get(control);
          const sequenceNumber = mod(start.sequenceNumber + item.index, 65536);
          const timestamp = mod(
            start.timestamp + item.index * definition.samplesPerPacket,
            2 ** 32,
          );
          const payload = Buffer.from(
            `${definition.kind}|generation=${generation}|index=${item.index}`,
            'utf8',
          );
          const packet = encodeRtpPacket({
            sequenceNumber,
            timestamp,
            ssrc: definition.ssrc,
            payloadType: definition.payloadType,
            marker: item.index === this.packetCount - 1,
            payload,
          });
          configured.udp.send(packet, configured.clientPort, session.clientHost);
        }
      }, item.atMs);
      session.timers.add(timer);
    }
  }

  handleTeardown(socket, request) {
    const session = this.sessionFromRequest(request);
    if (!session) {
      return this.send(socket, 454, 'Session Not Found', request);
    }
    this.cancelStreaming(session);
    session.state = 'TEARDOWN';
    for (const track of session.tracks.values()) {
      closeSocket(track.udp);
      closeSocket(track.rtcpUdp);
    }
    session.tracks.clear();
    return this.send(socket, 200, 'OK', request, {
      Session: session.id,
    });
  }
}

module.exports = {
  RtspLoopbackServer,
  extractControl,
  makeSendPlan,
  parseClientPort,
  parseNpt,
};
