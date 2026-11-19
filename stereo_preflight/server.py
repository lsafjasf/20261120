from __future__ import annotations

import base64
import json
import mimetypes
import threading
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .analysis import AnalysisOptions, analyze_video
from .export import ExportError, export_corrected, validate_corrections
from .media import MediaError, probe_video
from .preview import render_preview


STATIC_ROOT = Path(__file__).parent / "static"


class SessionStore:
    def __init__(self) -> None:
        self._items: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def put(self, report: dict[str, Any], media: Any) -> str:
        session_id = uuid.uuid4().hex
        with self._lock:
            self._items[session_id] = {"report": report, "media": media}
        return session_id

    def get(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            item = self._items.get(session_id)
        if item is None:
            raise KeyError(f"Unknown analysis session: {session_id}")
        return item


SESSIONS = SessionStore()


def _frame_flags(report: dict[str, Any], index: int) -> list[str]:
    frames = report.get("frames", [])
    return frames[index].get("flags", []) if 0 <= index < len(frames) else []


class Handler(BaseHTTPRequestHandler):
    server_version = "StereoPreflight/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[web] {self.address_string()} {fmt % args}")

    def _json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, error: Exception, status: int = HTTPStatus.BAD_REQUEST) -> None:
        self._json({"error": str(error), "type": type(error).__name__}, status)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 2_000_000:
            raise ValueError("Request body is too large")
        raw = self.rfile.read(length)
        payload = json.loads(raw or b"{}")
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        return payload

    def _static(self, path: str) -> None:
        names = {
            "/": "index.html",
            "/index.html": "index.html",
            "/app.js": "app.js",
            "/style.css": "style.css",
        }
        name = names.get(path)
        if name is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        target = STATIC_ROOT / name
        body = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type == "application/javascript":
            content_type += "; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path in {"/", "/index.html", "/app.js", "/style.css"}:
                self._static(parsed.path)
                return
            if parsed.path == "/api/health":
                self._json({"ok": True})
                return
            parts = [item for item in parsed.path.split("/") if item]
            if len(parts) == 5 and parts[:2] == ["api", "session"] and parts[3] == "frame":
                self._preview(parts[2], parts[4], parse_qs(parsed.query))
                return
            if len(parts) == 4 and parts[:2] == ["api", "session"] and parts[3] == "report":
                self._json(SESSIONS.get(parts[2])["report"])
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except BrokenPipeError:
            return
        except (KeyError, ValueError, IndexError, MediaError, OSError) as error:
            self._error(error)

    def _preview(self, session_id: str, frame_name: str, query: dict[str, list[str]]) -> None:
        if not frame_name.endswith(".png"):
            raise ValueError("Frame endpoint expects a .png suffix")
        index = int(frame_name[:-4])
        session = SESSIONS.get(session_id)
        report = session["report"]
        mode = query.get("mode", ["anaglyph"])[0]
        order = query.get("order", [report["eye_identity"].get("selected_order") or "ab"])[0]
        offset_left = int(query.get("offset_left", ["0"])[0])
        offset_right = int(query.get("offset_right", ["0"])[0])
        show_marks = query.get("marks", ["1"])[0] not in {"0", "false"}
        image, metadata = render_preview(
            report,
            session["media"],
            index,
            mode=mode,
            order=order,
            offset_left=offset_left,
            offset_right=offset_right,
            show_marks=show_marks,
        )
        metadata["flags"] = _frame_flags(report, index)
        encoded = base64.b64encode(json.dumps(metadata, ensure_ascii=False).encode("utf-8"))
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(image)))
        self.send_header("X-Preview-Meta", encoded.decode("ascii"))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(image)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            payload = self._body()
            if parsed.path == "/api/analyze":
                self._analyze(payload)
                return
            parts = [item for item in parsed.path.split("/") if item]
            if len(parts) == 4 and parts[:2] == ["api", "session"]:
                item = SESSIONS.get(parts[2])
                if parts[3] == "validate":
                    self._json(
                        validate_corrections(
                            item["report"],
                            order=str(payload.get("order", "auto")),
                            offset_left=int(payload.get("offset_left", 0)),
                            offset_right=int(payload.get("offset_right", 0)),
                        )
                    )
                    return
                if parts[3] == "export":
                    self._export(item["report"], payload)
                    return
            self.send_error(HTTPStatus.NOT_FOUND)
        except BrokenPipeError:
            return
        except (KeyError, ValueError, MediaError, ExportError, OSError) as error:
            self._error(error)

    def _analyze(self, payload: dict[str, Any]) -> None:
        path = str(payload.get("path", "")).strip()
        if not path:
            raise ValueError("Video path is required")
        packing = str(payload.get("packing", "sbs"))
        if packing not in {"sbs", "ou"}:
            raise ValueError("packing must be sbs or ou")
        assumed = payload.get("assumed_order") or None
        if assumed not in {None, "ab", "ba"}:
            raise ValueError("assumed_order must be ab, ba, or empty")
        max_px = payload.get("max_disparity_px")
        options = AnalysisOptions(
            packing=packing,
            max_disparity_px=int(max_px) if max_px not in {None, ""} else None,
            max_disparity_ratio=float(payload.get("max_disparity_ratio", 0.025)),
            max_vertical_px=float(payload.get("max_vertical_px", 2.0)),
            assumed_order=assumed,
        )
        report = analyze_video(path, options)
        media = probe_video(path)
        session_id = SESSIONS.put(report, media)
        report_path = None
        requested_report = str(payload.get("save_report", "")).strip()
        if requested_report:
            destination = Path(requested_report).expanduser().resolve()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            report_path = str(destination)
        self._json({"session_id": session_id, "report": report, "report_path": report_path})

    def _export(self, report: dict[str, Any], payload: dict[str, Any]) -> None:
        output = str(payload.get("output", "")).strip()
        if not output:
            raise ValueError("Output path is required")
        result = export_corrected(
            report,
            output,
            order=str(payload.get("order", "auto")),
            offset_left=int(payload.get("offset_left", 0)),
            offset_right=int(payload.get("offset_right", 0)),
            packaging=payload.get("packaging") or None,
            force=bool(payload.get("force", False)),
        )
        self._json(result.to_dict())


def serve(host: str = "127.0.0.1", port: int = 8765, *, open_browser: bool = False) -> None:
    if not STATIC_ROOT.is_dir():
        raise RuntimeError(f"Static UI assets not found: {STATIC_ROOT}")
    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    print(f"Stereo Preflight listening on {url}")
    print("Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        httpd.server_close()
