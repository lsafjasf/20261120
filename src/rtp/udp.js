'use strict';

const dgram = require('node:dgram');

function bindSocket(socket, port, host) {
  return new Promise((resolve, reject) => {
    const onError = (error) => {
      socket.removeListener('listening', onListening);
      reject(error);
    };
    const onListening = () => {
      socket.removeListener('error', onError);
      resolve();
    };
    socket.once('error', onError);
    socket.once('listening', onListening);
    socket.bind(port, host);
  });
}

function closeSocket(socket) {
  if (!socket) return;
  try {
    socket.close();
  } catch {
    // Socket was never bound or has already been closed.
  }
}

async function bindUdpPair(host = '127.0.0.1') {
  for (let attempt = 0; attempt < 64; attempt += 1) {
    const candidate = 30000 + 2 * Math.floor(Math.random() * 15000);
    const rtp = dgram.createSocket('udp4');
    const rtcp = dgram.createSocket('udp4');
    try {
      await bindSocket(rtp, candidate, host);
      await bindSocket(rtcp, candidate + 1, host);
      return { rtp, rtcp, rtpPort: candidate, rtcpPort: candidate + 1 };
    } catch {
      closeSocket(rtp);
      closeSocket(rtcp);
    }
  }
  throw new Error('Unable to reserve an even/odd UDP port pair');
}

module.exports = { bindUdpPair, closeSocket };
