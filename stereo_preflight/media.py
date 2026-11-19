from __future__ import annotations

import json
import struct
import subprocess
import zlib
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import BinaryIO, Iterator


class MediaError(RuntimeError):
    pass


@dataclass(frozen=True)
class MediaInfo:
    path: Path
    width: int
    height: int
    frame_rate: Fraction
    frame_count: int | None
    duration: float | None
    codec: str
    pixel_format: str
    has_audio: bool
    metadata: dict[str, str]

    @property
    def frame_rate_text(self) -> str:
        return f"{self.frame_rate.numerator}/{self.frame_rate.denominator}"


def _run(command: list[str]) -> bytes:
    proc = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode:
        message = proc.stderr.decode("utf-8", "replace").strip()
        raise MediaError(message or f"command failed: {' '.join(command)}")
    return proc.stdout


def _rate(value: str | None) -> Fraction:
    if not value or value in {"0/0", "N/A"}:
        return Fraction(0, 1)
    try:
        return Fraction(value)
    except (ValueError, ZeroDivisionError):
        return Fraction(0, 1)


def probe_video(path: str | Path) -> MediaInfo:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise MediaError(f"Input video not found: {source}")
    payload = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(source),
        ]
    )
    data = json.loads(payload)
    streams = data.get("streams", [])
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    if video is None:
        raise MediaError(f"No video stream in {source}")

    frame_rate = _rate(video.get("avg_frame_rate")) or _rate(video.get("r_frame_rate"))
    if frame_rate <= 0:
        raise MediaError(f"Could not determine frame rate for {source}")
    width = int(video["width"])
    height = int(video["height"])
    if width <= 0 or height <= 0:
        raise MediaError(f"Invalid video dimensions: {width}x{height}")

    frames_text = video.get("nb_frames")
    frame_count = int(frames_text) if frames_text and str(frames_text).isdigit() else None
    duration_text = data.get("format", {}).get("duration")
    duration = float(duration_text) if duration_text else None
    if frame_count is None and duration is not None:
        frame_count = round(duration * float(frame_rate))

    tags: dict[str, str] = {}
    for key, value in {**data.get("format", {}).get("tags", {}), **video.get("tags", {})}.items():
        if value is not None:
            tags[str(key).lower()] = str(value)
    return MediaInfo(
        path=source,
        width=width,
        height=height,
        frame_rate=frame_rate,
        frame_count=frame_count,
        duration=duration,
        codec=str(video.get("codec_name", "")),
        pixel_format=str(video.get("pix_fmt", "")),
        has_audio=any(item.get("codec_type") == "audio" for item in streams),
        metadata=tags,
    )


def eye_dimensions(info: MediaInfo, packing: str) -> tuple[int, int]:
    if packing == "sbs":
        if info.width % 2:
            raise MediaError(f"SBS width must be even, got {info.width}")
        return info.width // 2, info.height
    if packing == "ou":
        if info.height % 2:
            raise MediaError(f"OU height must be even, got {info.height}")
        return info.width, info.height // 2
    raise MediaError(f"Unknown packing: {packing}")


def _read_exact(stream: BinaryIO, size: int) -> bytes | None:
    buf = bytearray()
    while len(buf) < size:
        chunk = stream.read(size - len(buf))
        if not chunk:
            return None if not buf else bytes(buf)
        buf.extend(chunk)
    return bytes(buf)


def iter_frames(info: MediaInfo, *, start: int = 0, count: int | None = None) -> Iterator[tuple[int, bytes]]:
    """Yield packed RGB24 frames. Decoding starts at the beginning for exact frame numbers."""
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(info.path),
        "-map",
        "0:v:0",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-",
    ]
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdout is not None
    frame_size = info.width * info.height * 3
    emitted = 0
    index = 0
    try:
        while count is None or emitted < count:
            frame = _read_exact(proc.stdout, frame_size)
            if frame is None:
                break
            if len(frame) != frame_size:
                raise MediaError("Truncated frame from ffmpeg")
            if index >= start:
                yield index, frame
                emitted += 1
            index += 1
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=3)
        assert proc.stderr is not None
        error = proc.stderr.read().decode("utf-8", "replace").strip()
        proc.stderr.close()
        assert proc.stdout is not None
        proc.stdout.close()
        if proc.returncode not in (0, -9) and not emitted:
            raise MediaError(error or "ffmpeg frame decoding failed")


def split_eyes(frame: bytes, width: int, height: int, packing: str) -> tuple[bytes, bytes]:
    if packing == "sbs":
        eye_width = width // 2
        row_bytes = width * 3
        part = eye_width * 3
        first = bytearray(eye_width * height * 3)
        second = bytearray(eye_width * height * 3)
        for y in range(height):
            row = y * row_bytes
            out = y * part
            first[out : out + part] = frame[row : row + part]
            second[out : out + part] = frame[row + part : row + row_bytes]
        return bytes(first), bytes(second)
    if packing == "ou":
        half = width * (height // 2) * 3
        return frame[:half], frame[half : half * 2]
    raise MediaError(f"Unknown packing: {packing}")


def pack_eyes(first: bytes, second: bytes, width: int, height: int, packing: str) -> bytes:
    if packing == "sbs":
        eye_width = width // 2
        row_bytes = eye_width * 3
        output = bytearray(width * height * 3)
        for y in range(height):
            row = y * width * 3
            source = y * row_bytes
            output[row : row + row_bytes] = first[source : source + row_bytes]
            output[row + row_bytes : row + row_bytes * 2] = second[source : source + row_bytes]
        return bytes(output)
    if packing == "ou":
        return first + second
    raise MediaError(f"Unknown packing: {packing}")


def png_bytes(width: int, height: int, rgb: bytes) -> bytes:
    if len(rgb) != width * height * 3:
        raise ValueError("RGB buffer size does not match dimensions")
    signature = b"\x89PNG\r\n\x1a\n"

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    stride = width * 3
    raw = b"".join(b"\x00" + rgb[y * stride : (y + 1) * stride] for y in range(height))
    return signature + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")
