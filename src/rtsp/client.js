'use strict';

const net = require('node:net');
const {
  RtspMessageParser,
  formatRtspMessage,
  parseResponse,
} = require('./protocol');
const { parseSdp, parseRtpInfo } = require('./protocol');

class RtspClient {
  constructor(uri, { timeoutMs = 5000 } = {}) {
    this.uri = uri;
    this.timeoutMs = timeoutMs;
    this.cseq = 0;
    this.pending = new Map();
    this.transcript = [];
    this.socket = null;
    this.parser = new RtspMessageParser();
    this.sessionId = null;
    this.sdp = null;
    this.sdpTracks = [];
  }

  async connect() {
    const target = new URL(this.uri);
    this.socket = net.createConnection({
      host: target.hostname,
      port: Number.parseInt(target.port, 10) || 554,
    });
    this.socket.on('data', (chunk) => {
      for (const message of this.parser.feed(chunk)) {
        const response = parseResponse(message);
        this.transcript.push({
          direction: 'server->client',
          startLine: response.startLine,
          headers: response.headers,
          body: response.body,
        });
        const requestCseq = response.headers.cseq;
        const waiter = this.pending.get(requestCseq);
        if (waiter) {
          this.pending.delete(requestCseq);
          clearTimeout(waiter.timer);
          waiter.resolve(response);
        }
      }
    });
    this.socket.on('error', (error) => this.rejectAll(error));
    this.socket.on('close', () => this.rejectAll(new Error('RTSP connection closed')));

    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('RTSP connect timeout')), this.timeoutMs);
      this.socket.once('connect', () => {
        clearTimeout(timer);
        resolve();
      });
      this.socket.once('error', (error) => {
        clearTimeout(timer);
        reject(error);
      });
    });
    return this;
  }

  rejectAll(error) {
    for (const waiter of this.pending.values()) {
      clearTimeout(waiter.timer);
      waiter.reject(error);
    }
    this.pending.clear();
  }

  request(method, uri, headers = {}) {
    const cseq = String(++this.cseq);
    const requestHeaders = {
      CSeq: cseq,
      'User-Agent': 'Node-RTSP-Loopback-Client/1.0',
      ...headers,
    };
    const message = formatRtspMessage(`${method} ${uri} RTSP/1.0`, requestHeaders);
    this.transcript.push({
      direction: 'client->server',
      startLine: `${method} ${uri} RTSP/1.0`,
      headers: Object.fromEntries(
        Object.entries(requestHeaders).map(([name, value]) => [name.toLowerCase(), value]),
      ),
      body: '',
    });

    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(cseq);
        reject(new Error(`${method} request timed out`));
      }, this.timeoutMs);
      this.pending.set(cseq, { resolve, reject, timer });
      this.socket.write(message);
    });
  }

  async describe() {
    const response = await this.request('DESCRIBE', this.uri, {
      Accept: 'application/sdp',
    });
    if (response.statusCode !== 200) {
      throw new Error(`DESCRIBE failed: ${response.statusCode} ${response.statusMessage}`);
    }
    this.sdp = response.body;
    this.sdpTracks = parseSdp(response.body).tracks;
    return response;
  }

  async setup(control, clientPort) {
    const response = await this.request('SETUP', `${this.uri}/${control}`, {
      Transport: `RTP/AVP;unicast;client_port=${clientPort}-${clientPort + 1}`,
      ...(this.sessionId ? { Session: this.sessionId } : {}),
    });
    if (response.statusCode !== 200) {
      throw new Error(`SETUP ${control} failed: ${response.statusCode} ${response.statusMessage}`);
    }
    const sessionHeader = response.headers.session;
    if (sessionHeader) this.sessionId = sessionHeader.split(';')[0].trim();
    return response;
  }

  async play({ range = null } = {}) {
    const response = await this.request('PLAY', this.uri, {
      ...(this.sessionId ? { Session: this.sessionId } : {}),
      ...(range ? { Range: range } : {}),
    });
    if (response.statusCode !== 200) {
      throw new Error(`PLAY failed: ${response.statusCode} ${response.statusMessage}`);
    }
    return {
      response,
      rtpInfo: parseRtpInfo(response.headers['rtp-info']),
    };
  }

  async teardown() {
    if (!this.socket || this.socket.destroyed) return null;
    return this.request('TEARDOWN', this.uri, {
      ...(this.sessionId ? { Session: this.sessionId } : {}),
    });
  }

  close() {
    this.rejectAll(new Error('RTSP client closed'));
    if (this.socket) this.socket.destroy();
  }

  trackForControl(control) {
    return this.sdpTracks.find((track) => track.control === control) || null;
  }
}

module.exports = { RtspClient };
