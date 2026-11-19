"""Video frame loading helpers for the fused quality scoring pipeline.

Frames are decoded with the project-local static ffmpeg build and returned as
float32 numpy arrays.  All comparisons use the same 640x360 yuv420p grid.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
FFMPEG = ROOT / "tools" / "ffmpeg"
FFPROBE = ROOT / "tools" / "ffprobe"
CACHE_DIR = ROOT / "outputs" / ".frame_cache"


def _cached(path: Path, suffix: str, loader) -> np.ndarray:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{path.stem}-{suffix}.npy"
    if cache_file.exists():
        return np.load(cache_file)
    array = loader()
    np.save(cache_file, array)
    return array


def probe_video(path: str | Path) -> dict:
    """Return width, height, fps and frame count for a video file."""
    cmd = [
        str(FFPROBE),
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate,nb_frames",
        "-of",
        "json",
        str(path),
    ]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True)
    stream = json.loads(out.stdout)["streams"][0]
    num, den = stream["r_frame_rate"].split("/")
    return {
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "fps": float(num) / float(den),
        "frames": int(stream["nb_frames"]),
    }


def load_gray(path: str | Path) -> np.ndarray:
    """Decode a video to a T x H x W float32 luma array in [0, 1]."""
    path = Path(path)

    def _load() -> np.ndarray:
        info = probe_video(path)
        cmd = [
            str(FFMPEG),
            "-v",
            "error",
            "-i",
            str(path),
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ]
        raw = subprocess.run(cmd, check=True, capture_output=True).stdout
        frames = np.frombuffer(raw, dtype=np.uint8).reshape(
            info["frames"], info["height"], info["width"]
        )
        return frames.astype(np.float32) / 255.0

    return _cached(path, "gray", _load)


def load_yuv(path: str | Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Decode yuv420p into luma (T,H,W) and full-size chroma planes in [0, 1].

    Chroma planes are bilinearly upsampled back to the luma resolution so that
    every metric shares one coordinate system.
    """
    path = Path(path)

    def _load() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        info = probe_video(path)
        height, width = info["height"], info["width"]
        frame_count = info["frames"]
        chroma_h, chroma_w = height // 2, width // 2
        y_size = height * width
        c_size = chroma_h * chroma_w
        cmd = [
            str(FFMPEG),
            "-v",
            "error",
            "-i",
            str(path),
            "-f",
            "rawvideo",
            "-pix_fmt",
            "yuv420p",
            "-",
        ]
        raw = subprocess.run(cmd, check=True, capture_output=True).stdout
        frame_stride = y_size + 2 * c_size
        y = np.empty((frame_count, height, width), dtype=np.float32)
        u = np.empty((frame_count, height, width), dtype=np.float32)
        v = np.empty((frame_count, height, width), dtype=np.float32)
        for index in range(frame_count):
            base = index * frame_stride
            plane_y = np.frombuffer(raw, dtype=np.uint8, count=y_size, offset=base)
            plane_u = np.frombuffer(
                raw, dtype=np.uint8, count=c_size, offset=base + y_size
            ).reshape(chroma_h, chroma_w)
            plane_v = np.frombuffer(
                raw,
                dtype=np.uint8,
                count=c_size,
                offset=base + y_size + c_size,
            ).reshape(chroma_h, chroma_w)
            y[index] = plane_y.reshape(height, width).astype(np.float32) / 255.0
            u[index] = _upsample2x(plane_u).astype(np.float32) / 255.0
            v[index] = _upsample2x(plane_v).astype(np.float32) / 255.0
        return np.stack([y, u, v])

    stacked = _cached(path, "yuv", _load)
    return stacked[0], stacked[1], stacked[2]


def _upsample2x(plane: np.ndarray) -> np.ndarray:
    """Nearest-neighbour 2x upscale, matching ffmpeg's default chroma placement."""
    return np.repeat(np.repeat(plane, 2, axis=0), 2, axis=1)
