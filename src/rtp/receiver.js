'use strict';

const { performance } = require('node:perf_hooks');
const { parseRtpPacket } = require('./packet');
const { bindUdpPair, closeSocket } = require('./udp');

class UdpRtpReceiver {
  constructor({ name, host = '127.0.0.1' }) {
    this.name = name;
    this.host = host;
    this.socket = null;
    this.rtcpSocket = null;
    this.port = null;
    this.handlers = [];
    this.errors = [];
  }

  async bind() {
    const pair = await bindUdpPair(this.host);
    this.socket = pair.rtp;
    this.rtcpSocket = pair.rtcp;
    this.port = pair.rtpPort;
    this.socket.on('message', (buffer) => {
      const receivedAt = performance.now();
      try {
        const packet = parseRtpPacket(buffer);
        for (const handler of this.handlers) handler(packet, receivedAt);
      } catch (error) {
        this.errors.push(error);
      }
    });
    this.socket.on('error', (error) => this.errors.push(error));
    return this.port;
  }

  onPacket(handler) {
    this.handlers.push(handler);
  }

  close() {
    closeSocket(this.socket);
    closeSocket(this.rtcpSocket);
  }
}

module.exports = { UdpRtpReceiver };
