from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Iterator

import numpy as np


def find_ffmpeg() -> str:
    candidates = [
        os.environ.get("FFMPEG_BINARY"),
        shutil.which("ffmpeg"),
    ]
    try:
        import imageio_ffmpeg

        candidates.append(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        pass
    local_bins = sorted(
        Path.home().glob(
            ".local/lib/python*/site-packages/imageio_ffmpeg/binaries/ffmpeg-*"
        )
    )
    candidates.extend(str(path) for path in local_bins)
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError("ffmpeg not found; set FFMPEG_BINARY")


def ffprobe_video(path: str | Path) -> dict:
    ffmpeg = Path(find_ffmpeg())
    probe = ffmpeg.with_name(ffmpeg.name.replace("ffmpeg", "ffprobe"))
    if not probe.is_file():
        probe = Path(shutil.which("ffprobe") or "")
    if not probe.is_file():
        return _probe_with_ffmpeg(str(ffmpeg), Path(path))
    cmd = [
        str(probe),
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate,avg_frame_rate,nb_frames,codec_name,pix_fmt",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ]
    raw = subprocess.check_output(cmd, text=True)
    data = json.loads(raw)
    stream = data["streams"][0]
    rate = stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "0/1"
    num, den = (int(part) for part in rate.split("/"))
    fps = num / den if den else 0.0
    duration = float(data.get("format", {}).get("duration") or 0.0)
    return {
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "fps": fps,
        "duration": duration,
        "codec": stream.get("codec_name"),
        "pix_fmt": stream.get("pix_fmt"),
        "frame_count": int(stream.get("nb_frames") or round(duration * fps)),
    }


def _probe_with_ffmpeg(ffmpeg: str, path: Path) -> dict:
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    text = proc.stdout + "\n" + proc.stderr
    duration_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    video_match = re.search(
        r"Video:\s*([^,\s]+).*?(\d{2,5})x(\d{2,5}).*?"
        r"([\d.]+)\s+fps",
        text,
    )
    if not duration_match or not video_match:
        raise RuntimeError(f"cannot parse video metadata from {path}")
    hours, minutes, seconds = duration_match.groups()
    duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    codec, width, height, fps_text = video_match.groups()
    fps = float(fps_text)
    pix_match = re.search(r"Video:.*?,\s*(yuv\w+|rgb\w+|gray\w+)", text)
    return {
        "width": int(width),
        "height": int(height),
        "fps": fps,
        "duration": duration,
        "codec": codec,
        "pix_fmt": pix_match.group(1) if pix_match else "unknown",
        "frame_count": int(round(duration * fps)),
    }


def iter_rgb_frames(
    path: str | Path, width: int, height: int
) -> Iterator[np.ndarray]:
    cmd = [
        find_ffmpeg(),
        "-v",
        "error",
        "-i",
        str(path),
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "pipe:1",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdout is not None
    size = width * height * 3
    closed_early = False
    try:
        while True:
            raw = proc.stdout.read(size)
            if len(raw) < size:
                break
            yield np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3)
    except GeneratorExit:
        closed_early = True
        raise
    finally:
        proc.stdout.close()
        if closed_early and proc.poll() is None:
            proc.terminate()
        stderr = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        if proc.stderr:
            proc.stderr.close()
        code = proc.wait()
        if code and not closed_early:
            raise RuntimeError(f"ffmpeg decode failed ({code}): {stderr[-2000:]}")
