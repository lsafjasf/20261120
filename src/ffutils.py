"""Thin wrappers around the bundled ffprobe/ffmpeg binaries."""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FFMPEG = str(ROOT / "tools" / "ffmpeg")
FFPROBE = str(ROOT / "tools" / "ffprobe")


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, **kw)


def probe_streams(path: str | Path) -> list[dict]:
    cmd = [
        FFPROBE, "-v", "error", "-print_format", "json",
        "-show_streams", str(path),
    ]
    return json.loads(run(cmd).stdout)["streams"]


def frame_table(path: str | Path, stream: str = "v:0") -> list[dict]:
    """Per-frame pts/pts_time/pict_type/key_frame for a video stream."""
    cmd = [
        FFPROBE, "-v", "error", "-print_format", "json",
        "-select_streams", stream,
        "-show_frames", str(path),
    ]
    return json.loads(run(cmd).stdout)["frames"]


def packet_table(path: str | Path) -> list[dict]:
    """Per-packet metadata for every stream (used for audio codec checks)."""
    cmd = [
        FFPROBE, "-v", "error", "-print_format", "json",
        "-show_packets", str(path),
    ]
    return json.loads(run(cmd).stdout)["packets"]


def count_frames(path: str | Path, stream: str = "v:0") -> int:
    cmd = [
        FFPROBE, "-v", "error", "-select_streams", stream,
        "-count_frames", "-show_entries", "stream=nb_read_frames",
        "-of", "csv=p=0", str(path),
    ]
    return int(run(cmd).stdout.decode().strip())
