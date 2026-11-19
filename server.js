// Mock license server + static file server for the EME persistent-license demo.
// Counts license requests so the test runner can prove no duplicate request
// is issued when a persisted session is restored.
const http = require('http');
const fs = require('fs');
const path = require('path');

const PUBLIC_DIR = path.join(__dirname, 'public');

// Fixed ClearKey key material (demo only).
const KEY_ID = 'oR5w1fZgQd-5z5z5z5z5z5w'; // base64url kid
const KEY = 'uQz8m0yV2y0hHh9w6x8m0Q';   // base64url key

const stats = {
  licenseRequests: 0,
  requests: [],
};

function clearKeyLicense() {
  return {
    keys: [{ kty: 'oct', kid: KEY_ID, k: KEY }],
    type: 'temporary',
  };
}

const SCENARIOS = {
  usable: {
    status: 200,
    body: () => ({
      license: clearKeyLicense(),
      policy: { keyStatus: 'usable' },
    }),
  },
  expired: {
    status: 200,
    body: () => ({
      license: clearKeyLicense(),
      policy: {
        keyStatus: 'expired',
        message: '许可已于 2026-11-01T00:00:00Z 过期 (LICENSE_EXPIRED)',
      },
    }),
  },
  'output-restricted': {
    status: 200,
    body: () => ({
      license: clearKeyLicense(),
      policy: {
        keyStatus: 'output-restricted',
        message: '输出链路不满足内容保护策略要求 (OUTPUT_RESTRICTED: HDCP)',
      },
    }),
  },
  'server-error': {
    status: 500,
    body: () => ({
      error: {
        code: 'LICENSE_SERVER_ERROR',
        message: '许可服务器内部错误 (mock HTTP 500)',
      },
    }),
  },
};

function sendJson(res, status, obj) {
  const data = JSON.stringify(obj);
  res.writeHead(status, { 'content-type': 'application/json; charset=utf-8' });
  res.end(data);
}

function readBody(req) {
  return new Promise((resolve) => {
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
  });
}

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
};

function createServer() {
  return http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');

    if (url.pathname === '/api/license' && req.method === 'POST') {
      const scenario = url.searchParams.get('scenario') || 'usable';
      const body = await readBody(req);
      stats.licenseRequests += 1;
      stats.requests.push({ scenario, at: Date.now(), body });
      const handler = SCENARIOS[scenario] || SCENARIOS.usable;
      sendJson(res, handler.status, handler.body());
      return;
    }
    if (url.pathname === '/api/stats') {
      sendJson(res, 200, stats);
      return;
    }
    if (url.pathname === '/api/stats/reset' && req.method === 'POST') {
      stats.licenseRequests = 0;
      stats.requests.length = 0;
      sendJson(res, 200, { ok: true });
      return;
    }

    // Static files.
    const filePath = path.join(PUBLIC_DIR, url.pathname === '/' ? 'index.html' : url.pathname);
    if (!filePath.startsWith(PUBLIC_DIR)) {
      res.writeHead(403); res.end(); return;
    }
    fs.readFile(filePath, (err, data) => {
      if (err) { res.writeHead(404); res.end('not found'); return; }
      res.writeHead(200, { 'content-type': MIME[path.extname(filePath)] || 'application/octet-stream' });
      res.end(data);
    });
  });
}

if (require.main === module) {
  const port = Number(process.env.PORT || 8080);
  createServer().listen(port, () => console.log(`demo server on http://127.0.0.1:${port}`));
}

module.exports = { createServer, stats };
