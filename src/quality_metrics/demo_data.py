"""Synthetic reference sequence generation and split-bitrate encoding.

The demo does not depend on any proprietary footage: a deterministic 180
frame / 320x180 / 30 fps sequence is rendered with moving shapes, gradients
and a high-noise burst, then encoded in two CRF segments so the resulting
video has an unambiguous high-/low-bitrate boundary at frame 90.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass

import cv2
import numpy as np

WIDTH = 320
HEIGHT = 180
FPS = 30
FRAME_COUNT = 180
BOUNDARY_FRAME = 90
NOISE_FRAMES = list(range(45, 60))
HIGH_CRF = 16
LOW_CRF = 42


@dataclass(frozen=True)
class DemoSpec:
    width: int = WIDTH
    height: int = HEIGHT
    fps: int = FPS
    frame_count: int = FRAME_COUNT
    boundary_frame: int = BOUNDARY_FRAME
    noise_frames: tuple[int, ...] = tuple(NOISE_FRAMES)
    high_crf: int = HIGH_CRF
    low_crf: int = LOW_CRF


def _render_frame(index: int, spec: DemoSpec, rng: np.random.Generator) -> np.ndarray:
    h, w = spec.height, spec.width
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    # Smooth low-texture background gradient drifting with the frame index.
    r = 90 + 70 * np.sin((xx / w) * np.pi + index * 0.05)
    g = 90 + 70 * np.cos((yy / h) * np.pi + index * 0.04)
    b = 120 + 60 * np.sin(((xx + yy) / (w + h)) * 2 * np.pi + index * 0.03)
    frame = np.stack([b, g, r], axis=-1)

    # Localised high-texture moving patch: compression damage concentrates
    # here, which is what lets the three metrics agree on the hot regions.
    patch = np.zeros((80, 100, 3), dtype=np.uint8)
    for py in range(80):
        for px in range(100):
            level = 255 if ((px // 8) + (py // 8)) % 2 else 40
            patch[py, px] = (level, level, level)
    patch_x = int(30 + index * 1.2) % w
    patch_y = 24
    x_end = min(w, patch_x + 100)
    y_end = min(h, patch_y + 80)
    frame[patch_y:y_end, patch_x:x_end] = patch[
        : y_end - patch_y, : x_end - patch_x
    ]

    # Spoked circle and text add other structured high-frequency regions.
    cx = int(260 - index) % w
    cy = 128
    cv2.circle(frame, (cx, cy), 34, (255, 255, 255), thickness=-1)
    for angle in range(0, 360, 30):
        spoke_x = int(cx + 34 * np.cos(np.radians(angle)))
        spoke_y = int(cy + 34 * np.sin(np.radians(angle)))
        cv2.line(frame, (cx, cy), (spoke_x, spoke_y), (0, 0, 0), 1)
    frame = np.clip(frame, 0, 255).astype(np.uint8)
    cv2.putText(
        frame, "QUALITY", (36, 164), cv2.FONT_HERSHEY_SIMPLEX,
        0.8, (255, 255, 255), 2,
    )

    if index in spec.noise_frames:
        noise = rng.normal(0.0, 28.0, frame.shape)
        frame = np.clip(frame.astype(np.float32) + noise, 0, 255).astype(np.uint8)
    return frame


def generate_reference(out_dir: str, spec: DemoSpec | None = None) -> dict:
    spec = spec or DemoSpec()
    frame_dir = os.path.join(out_dir, "reference_frames")
    os.makedirs(frame_dir, exist_ok=True)
    rng = np.random.default_rng(20261120)
    for index in range(spec.frame_count):
        frame = _render_frame(index, spec, rng)
        path = os.path.join(frame_dir, f"frame_{index:04d}.png")
        cv2.imwrite(path, frame)
    manifest = {
        "width": spec.width,
        "height": spec.height,
        "fps": spec.fps,
        "frame_count": spec.frame_count,
        "boundary_frame": spec.boundary_frame,
        "noise_frames": list(spec.noise_frames),
        "high_crf": spec.high_crf,
        "low_crf": spec.low_crf,
    }
    with open(os.path.join(out_dir, "demo_manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2)
    return manifest


def _encode_segment(
    frame_dir: str, spec: DemoSpec, expr: str, crf: int, out_path: str
) -> None:
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-framerate", str(spec.fps),
        "-i", os.path.join(frame_dir, "frame_%04d.png"),
        "-vf", f"select='{expr}'",
        "-vsync", "vfr",
        "-c:v", "libx264", "-preset", "medium",
        "-crf", str(crf), "-pix_fmt", "yuv420p",
        out_path,
    ]
    subprocess.run(cmd, check=True)


def encode_split_bitrate(out_dir: str, spec: DemoSpec | None = None) -> str:
    """Encode the two CRF segments and concatenate them without re-encoding."""
    spec = spec or DemoSpec()
    frame_dir = os.path.join(out_dir, "reference_frames")
    high_path = os.path.join(out_dir, "_seg_high.mp4")
    low_path = os.path.join(out_dir, "_seg_low.mp4")
    _encode_segment(
        frame_dir, spec, f"lt(n\\,{spec.boundary_frame})", spec.high_crf, high_path
    )
    _encode_segment(
        frame_dir, spec, f"gte(n\\,{spec.boundary_frame})", spec.low_crf, low_path
    )
    list_path = os.path.join(out_dir, "_concat.txt")
    with open(list_path, "w") as fh:
        fh.write(f"file '{os.path.abspath(high_path)}'\n")
        fh.write(f"file '{os.path.abspath(low_path)}'\n")
    encoded_path = os.path.join(out_dir, "encoded.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", list_path, "-c", "copy", encoded_path],
        check=True,
    )
    for path in (high_path, low_path, list_path):
        os.remove(path)
    return encoded_path
