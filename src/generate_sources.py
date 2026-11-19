#!/usr/bin/env python3
"""Generate deterministic static and high-motion YUV test sequences."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
MEDIA = ROOT / "artifacts" / "media"
WIDTH = 640
HEIGHT = 360
FPS = 30
FRAMES = 90


def clip_u8(values: np.ndarray) -> np.ndarray:
    return np.clip(np.rint(values), 0, 255).astype(np.uint8)


def make_scene(width: int, height: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    yy, xx = np.mgrid[0:height, 0:width]
    x = xx.astype(np.float64)
    y = yy.astype(np.float64)

    waves = (
        0.21 * np.sin(2 * np.pi * (x * 0.073 + y * 0.041))
        + 0.15 * np.cos(2 * np.pi * (x * 0.173 - y * 0.113))
        + 0.10 * np.sin(2 * np.pi * (x * 0.331 + y * 0.277))
    )
    fine_grid = 0.09 * np.sign(np.sin(2 * np.pi * x / 12) * np.sin(2 * np.pi * y / 12))
    blocks = 0.13 * (((xx // 40) + (yy // 36)) % 2)
    radius2 = (x - width * 0.70) ** 2 + (y - height * 0.40) ** 2
    zoneplate = 0.11 * np.cos(radius2 * 0.018)
    ramp = 0.20 * (x / width - 0.5)
    luma = 0.52 + waves + fine_grid + blocks - 0.065 + zoneplate + ramp
    luma = clip_u8(16 + 219 * np.clip(luma, 0, 1))

    u_plane = 128 + 21 * np.sin(2 * np.pi * (x * 0.017 + y * 0.023))
    v_plane = 128 + 18 * np.cos(2 * np.pi * (x * 0.026 - y * 0.019))
    return luma, clip_u8(u_plane), clip_u8(v_plane)


def to_yuv420(y: np.ndarray, u: np.ndarray, v: np.ndarray) -> bytes:
    if y.shape[0] % 2 or y.shape[1] % 2:
        raise ValueError("YUV420 requires even dimensions")
    u420 = np.rint(u.reshape(y.shape[0] // 2, 2, y.shape[1] // 2, 2).mean((1, 3)))
    v420 = np.rint(v.reshape(y.shape[0] // 2, 2, y.shape[1] // 2, 2).mean((1, 3)))
    return y.tobytes() + clip_u8(u420).tobytes() + clip_u8(v420).tobytes()


def make_static(scene: tuple[np.ndarray, np.ndarray, np.ndarray]) -> bytes:
    y, u, v = scene
    return to_yuv420(y, u, v) * FRAMES


def make_motion(scene: tuple[np.ndarray, np.ndarray, np.ndarray]) -> bytes:
    y, u, v = scene
    padded_y = np.concatenate([y, y[:, :96]], axis=1)
    padded_u = np.concatenate([u, u[:, :96]], axis=1)
    padded_v = np.concatenate([v, v[:, :96]], axis=1)
    chunks: list[bytes] = []
    yy, xx = np.mgrid[0:HEIGHT, 0:WIDTH]
    for index in range(FRAMES):
        shift_x = (index * 9) % 96
        shift_y = int(round(11 * np.sin(index * 0.13)))
        frame_y = np.roll(padded_y[:, shift_x : shift_x + WIDTH], shift_y, axis=0)
        frame_u = np.roll(padded_u[:, shift_x : shift_x + WIDTH], shift_y, axis=0)
        frame_v = np.roll(padded_v[:, shift_x : shift_x + WIDTH], shift_y, axis=0)
        cx = 70 + ((index * 17) % (WIDTH - 140))
        cy = HEIGHT // 2 + int(round(55 * np.cos(index * 0.21)))
        disk = (xx - cx) ** 2 + (yy - cy) ** 2 <= 34**2
        frame_y = frame_y.copy()
        frame_v = frame_v.copy()
        frame_y[disk] = clip_u8(216 + 18 * np.sin(index * 0.7))
        frame_v[disk] = 184
        chunks.append(to_yuv420(frame_y, frame_u, frame_v))
    return b"".join(chunks)


def write_and_wrap(name: str, payload: bytes, ffmpeg: Path) -> Path:
    raw_path = MEDIA / f"{name}_{WIDTH}x{HEIGHT}_{FPS}.yuv"
    mkv_path = MEDIA / f"{name}.mkv"
    raw_path.write_bytes(payload)
    command = [
        str(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "yuv420p",
        "-s",
        f"{WIDTH}x{HEIGHT}",
        "-r",
        str(FPS),
        "-i",
        str(raw_path),
        "-c:v",
        "ffv1",
        "-level",
        "3",
        "-g",
        "1",
        "-pix_fmt",
        "yuv420p",
        str(mkv_path),
    ]
    import subprocess

    subprocess.run(command, check=True)
    metadata = {"raw": raw_path, "mkv": mkv_path}
    return metadata


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--ffmpeg", type=Path, default=ROOT / ".tools/ffmpeg-dist/ffmpeg")
    args = parser.parse_args()
    MEDIA.mkdir(parents=True, exist_ok=True)
    scene = make_scene(WIDTH, HEIGHT)
    outputs = {
        "static": write_and_wrap("static", make_static(scene), args.ffmpeg),
        "motion": write_and_wrap("motion", make_motion(scene), args.ffmpeg),
    }
    metadata = {
        "width": WIDTH,
        "height": HEIGHT,
        "fps": FPS,
        "frames": FRAMES,
        "duration_s": FRAMES / FPS,
        "pix_fmt": "yuv420p",
        "source_codec": "ffv1",
        "scene": "deterministic multi-frequency texture with disk overlay",
        "motion": "9 px/frame horizontal pan, sinusoidal vertical pan, moving disk",
        "outputs": {
            key: {
                "raw": str(value["raw"].relative_to(ROOT)),
                "ffv1": str(value["mkv"].relative_to(ROOT)),
            }
            for key, value in outputs.items()
        },
    }
    (MEDIA / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
