"""Player-side probes for rewritten PSSH and media container structure."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .media import MediaError, default_kid, parse_pssh_boxes, top_level_boxes
from .pssh import kid_hex, pssh_summary


@dataclass
class PlayerProbe:
    parsed: bool
    pssh_count: int = 0
    providers: list[str] = field(default_factory=list)
    pssh: list[dict[str, Any]] = field(default_factory=list)
    default_kid: str | None = None
    header_kid_matches_default: bool = False
    payload_kid_matches_default: bool = False
    top_level_boxes: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    ffprobe: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "parsed": self.parsed,
            "pssh_count": self.pssh_count,
            "providers": self.providers,
            "pssh": self.pssh,
            "default_kid": self.default_kid,
            "header_kid_matches_default": self.header_kid_matches_default,
            "payload_kid_matches_default": self.payload_kid_matches_default,
            "top_level_boxes": self.top_level_boxes,
            "errors": self.errors,
            "ffprobe": self.ffprobe,
        }

    def require_ready(self) -> None:
        if not self.parsed:
            raise MediaError("player could not parse the media: " + "; ".join(self.errors))
        if not self.header_kid_matches_default:
            raise MediaError("PSSH header KID does not match tenc.default_KID")
        if not self.payload_kid_matches_default:
            raise MediaError("PSSH payload KID does not match tenc.default_KID")


def probe_media(data: bytes, path: str | Path | None = None) -> PlayerProbe:
    probe = PlayerProbe(parsed=False)
    try:
        top = top_level_boxes(data)
        pssh_boxes = parse_pssh_boxes(data)
        default = default_kid(data)
    except (MediaError, ValueError) as exc:
        probe.errors.append(str(exc))
        return probe
    probe.parsed = True
    probe.pssh_count = len(pssh_boxes)
    probe.providers = [box.system_name for box in pssh_boxes]
    probe.pssh = [pssh_summary(box) for box in pssh_boxes]
    probe.default_kid = kid_hex(default)
    probe.header_kid_matches_default = bool(pssh_boxes) and all(
        box.kids == (default,) for box in pssh_boxes
    )
    probe.payload_kid_matches_default = bool(pssh_boxes) and all(
        box.provider_kids() == (default,) for box in pssh_boxes
    )
    probe.top_level_boxes = [
        {
            "type": box.type.decode("latin-1"),
            "offset": box.start,
            "size": box.size,
        }
        for box in top
    ]
    if path is not None:
        probe.ffprobe = ffprobe_summary(Path(path))
    return probe


def ffprobe_summary(path: Path) -> dict[str, Any]:
    executable = shutil.which("ffprobe")
    if not executable:
        return {"available": False, "ok": None}
    command = [
        executable,
        "-v",
        "error",
        "-show_entries",
        (
            "format=format_name,duration:"
            "stream=index,codec_name,codec_type,width,height,nb_frames"
        ),
        "-of",
        "json",
        str(path),
    ]
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    try:
        payload = json.loads(completed.stdout) if completed.stdout.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    streams = payload.get("streams", [])
    signature = hashlib.sha256(
        json.dumps(streams, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    stderr_lines = completed.stderr.strip().splitlines()
    return {
        "available": True,
        "ok": completed.returncode == 0 and bool(streams),
        "format": payload.get("format", {}),
        "streams": streams,
        "stream_signature": signature,
        "stderr_tail": stderr_lines[-3:],
    }
