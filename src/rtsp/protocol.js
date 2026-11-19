'use strict';

class RtspMessageParser {
  constructor() {
    this.buffer = Buffer.alloc(0);
  }

  feed(chunk) {
    this.buffer = Buffer.concat([this.buffer, chunk]);
    const messages = [];

    while (true) {
      const headerEnd = this.buffer.indexOf('\r\n\r\n');
      if (headerEnd === -1) break;

      const headerText = this.buffer.subarray(0, headerEnd).toString('ascii');
      const lines = headerText.split('\r\n');
      const startLine = lines.shift();
      const headers = {};
      for (const line of lines) {
        const separator = line.indexOf(':');
        if (separator === -1) continue;
        const name = line.slice(0, separator).trim().toLowerCase();
        const value = line.slice(separator + 1).trim();
        headers[name] = headers[name] ? `${headers[name]}, ${value}` : value;
      }

      const contentLength = Number.parseInt(headers['content-length'] || '0', 10);
      if (!Number.isFinite(contentLength) || contentLength < 0) {
        throw new Error(`Invalid RTSP Content-Length: ${headers['content-length']}`);
      }
      const messageEnd = headerEnd + 4 + contentLength;
      if (this.buffer.length < messageEnd) break;

      const body = this.buffer.subarray(headerEnd + 4, messageEnd).toString('utf8');
      this.buffer = this.buffer.subarray(messageEnd);
      messages.push({ startLine, headers, body });
    }

    return messages;
  }
}

function parseRequest(message) {
  const [method, uri, version] = message.startLine.split(' ');
  if (!method || !uri || !version) {
    throw new Error(`Malformed RTSP request line: ${message.startLine}`);
  }
  return { ...message, method, uri, version };
}

function parseResponse(message) {
  const match = /^RTSP\/\d\.\d\s+(\d{3})\s*(.*)$/.exec(message.startLine);
  if (!match) throw new Error(`Malformed RTSP response line: ${message.startLine}`);
  return {
    ...message,
    statusCode: Number.parseInt(match[1], 10),
    statusMessage: match[2],
  };
}

function formatRtspMessage(startLine, headers, body = '') {
  const bodyBuffer = Buffer.isBuffer(body) ? body : Buffer.from(body);
  const outputHeaders = { ...headers };
  if (bodyBuffer.length > 0 || !('Content-Length' in outputHeaders)) {
    outputHeaders['Content-Length'] = String(bodyBuffer.length);
  }
  const headerLines = Object.entries(outputHeaders).map(([name, value]) => `${name}: ${value}`);
  return Buffer.concat([
    Buffer.from(`${startLine}\r\n${headerLines.join('\r\n')}\r\n\r\n`, 'utf8'),
    bodyBuffer,
  ]);
}

function formatRtspResponse(statusCode, statusMessage, headers, body = '') {
  return formatRtspMessage(`RTSP/1.0 ${statusCode} ${statusMessage}`, headers, body);
}

function parseSdp(sdp) {
  const lines = sdp.split(/\r?\n/).filter(Boolean);
  const tracks = [];
  let track = null;

  for (const line of lines) {
    if (line.startsWith('m=')) {
      const [media, port, protocol, payloadType] = line.slice(2).split(' ');
      track = {
        media,
        port: Number.parseInt(port, 10),
        protocol,
        payloadType: Number.parseInt(payloadType, 10),
        attributes: [],
      };
      tracks.push(track);
      continue;
    }
    if (!track) continue;
    if (line.startsWith('a=control:')) track.control = line.slice('a=control:'.length);
    if (line.startsWith('a=rtpmap:')) {
      const match = /^a=rtpmap:(\d+)\s+([^/]+)\/(\d+)(?:\/(\d+))?/.exec(line);
      if (match) {
        track.rtpmap = {
          payloadType: Number.parseInt(match[1], 10),
          encoding: match[2],
          clockRate: Number.parseInt(match[3], 10),
          channels: match[4] ? Number.parseInt(match[4], 10) : 1,
        };
      }
    }
    track.attributes.push(line);
  }

  return { tracks, raw: sdp };
}

function parseRtpInfo(value) {
  if (!value) return [];
  return value.split(',').map((entry) => {
    const [urlPart, ...parameters] = entry.trim().split(';');
    const result = { url: urlPart.replace(/^url=/, '') };
    for (const parameter of parameters) {
      const separator = parameter.indexOf('=');
      if (separator === -1) continue;
      const name = parameter.slice(0, separator).trim();
      const parameterValue = parameter.slice(separator + 1).trim();
      if (name === 'seq' || name === 'rtptime') {
        result[name] = Number.parseInt(parameterValue, 10);
      } else {
        result[name] = parameterValue;
      }
    }
    return result;
  });
}

function formatRtpInfo(entries) {
  return entries
    .map(({ url, seq, rtptime }) => `url=${url};seq=${seq};rtptime=${rtptime}`)
    .join(',');
}

module.exports = {
  RtspMessageParser,
  formatRtpInfo,
  formatRtspMessage,
  formatRtspResponse,
  parseRequest,
  parseResponse,
  parseRtpInfo,
  parseSdp,
};
