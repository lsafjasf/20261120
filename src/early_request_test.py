#!/usr/bin/env python3
import hashlib
import http.client
import json
import select
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from media_utils import ROOT, write_json

PART = ROOT / "artifacts/lhls/part-000.m4s"
RELEASE_DELAY = 0.35
PARTIAL_BYTES = 2048


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def _headers(self, length):
        self.send_response(200)
        self.send_header("Content-Type", "audio/mp4")
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Release-Delay-Ms", str(int(RELEASE_DELAY * 1000)))
        self.end_headers()
        self.wfile.flush()

    def do_GET(self):
        data = PART.read_bytes()
        if self.path == "/hinted-part":
            self._headers(len(data))
            time.sleep(RELEASE_DELAY)
            self.wfile.write(data)
            self.wfile.flush()
        elif self.path == "/partial-part":
            self._headers(len(data))
            split = min(PARTIAL_BYTES, len(data) - 1)
            self.wfile.write(data[:split])
            self.wfile.flush()
            time.sleep(RELEASE_DELAY)
            self.wfile.write(data[split:])
            self.wfile.flush()
        else:
            self.send_error(404)


def fetch(port, path):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    started = time.monotonic()
    connection.request("GET", path)
    sock = connection.sock
    response = connection.getresponse()
    headers_at = time.monotonic()
    has_early = bool(select.select([sock], [], [], 0.12)[0])
    early = b""
    if has_early:
        early = response.read1(4096)
    early_at = time.monotonic()
    body = early + response.read()
    completed = time.monotonic()
    connection.close()
    expected = PART.read_bytes()
    return {
        "path": path,
        "status": response.status,
        "content_length": int(response.getheader("Content-Length", "0")),
        "early_body_observable": has_early,
        "early_body_bytes": len(early),
        "body_complete_before_release": len(body) == len(expected) and completed < started + 0.2,
        "total_bytes": len(body),
        "sha256_match": hashlib.sha256(body).hexdigest() == hashlib.sha256(expected).hexdigest(),
        "headers_ms": round((headers_at - started) * 1000, 3),
        "early_observation_ms": round((early_at - started) * 1000, 3),
        "completion_ms": round((completed - started) * 1000, 3),
    }


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        hinted = fetch(server.server_port, "/hinted-part")
        partial = fetch(server.server_port, "/partial-part")
    finally:
        server.shutdown()
        server.server_close()

    result = {
        "release_delay_s": RELEASE_DELAY,
        "hinted_early_request": hinted,
        "partial_early_request": partial,
        "checks": {
            "hinted_request_has_no_body_before_release": (
                hinted["early_body_bytes"] == 0
                and not hinted["body_complete_before_release"]
            ),
            "hinted_request_completes_with_exact_part": (
                hinted["status"] == 200
                and hinted["total_bytes"] == hinted["content_length"]
                and hinted["sha256_match"]
            ),
            "partial_request_exposes_incomplete_bytes": (
                partial["early_body_bytes"] > 0
                and partial["early_body_bytes"] < partial["content_length"]
                and not partial["body_complete_before_release"]
            ),
            "partial_request_eventually_completes": (
                partial["status"] == 200 and partial["sha256_match"]
            ),
        },
    }
    result["all_checks_pass"] = all(result["checks"].values())
    write_json(ROOT / "artifacts/results/early_request_http.json", result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
