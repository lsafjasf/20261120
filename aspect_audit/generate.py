from __future__ import annotations

import json
import math
import subprocess
import csv
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .media import find_ffmpeg


@dataclass(frozen=True)
class ClipSpec:
    name: str
    duration: float
    fade_frames: int
    initial_layout: str
    switch_frame: int
    black_start: int
    black_end: int
    subtitle_start: int
    subtitle_end: int
    crf: int = 18

    @property
    def fps(self) -> float:
        return 24.0

    @property
    def frame_count(self) -> int:
        return int(round(self.duration * self.fps))


CLIPS = {
    "long": ClipSpec(
        name="long",
        duration=12.0,
        fade_frames=18,
        initial_layout="full",
        switch_frame=72,
        black_start=60,
        black_end=71,
        subtitle_start=204,
        subtitle_end=239,
        crf=18,
    ),
    "short": ClipSpec(
        name="short",
        duration=7.5,
        fade_frames=12,
        initial_layout="pillarbox",
        switch_frame=66,
        black_start=60,
        black_end=65,
        subtitle_start=126,
        subtitle_end=150,
        crf=21,
    ),
}


def _base_scene(width: int, height: int, frame: int, fps: float) -> np.ndarray:
    t = frame / fps
    y = np.linspace(0.0, 1.0, height, dtype=np.float32)[:, None, None]
    sky_top = np.array([42, 116, 196], dtype=np.float32)
    sky_bottom = np.array([218, 190, 142], dtype=np.float32)
    canvas = np.broadcast_to(
        sky_top * (1.0 - y) + sky_bottom * y, (height, width, 3)
    ).copy()

    # Distant hills and foreground are intentionally bright at every edge so
    # the detector cannot mistake active picture for matte.
    hill = np.array(
        [
            [0, int(height * 0.56)],
            [int(width * 0.13), int(height * 0.31)],
            [int(width * 0.27), int(height * 0.57)],
            [int(width * 0.43), int(height * 0.26)],
            [int(width * 0.59), int(height * 0.55)],
            [int(width * 0.75), int(height * 0.30)],
            [width - 1, int(height * 0.53)],
            [width - 1, height - 1],
            [0, height - 1],
        ],
        dtype=np.int32,
    )
    cv2.fillPoly(canvas, [hill], (79, 121, 98))
    field = np.array(
        [
            [0, int(height * 0.66)],
            [width - 1, int(height * 0.61)],
            [width - 1, height - 1],
            [0, height - 1],
        ],
        dtype=np.int32,
    )
    cv2.fillPoly(canvas, [field], (86, 138, 64))

    sun_x = int(width * (0.76 + 0.015 * math.sin(t * 0.35)))
    sun_y = int(height * 0.20)
    cv2.circle(canvas, (sun_x, sun_y), max(16, width // 42), (244, 230, 172), -1, cv2.LINE_AA)

    road_shift = int((t * 95) % width)
    cv2.line(
        canvas,
        (-road_shift, height),
        (int(width * 0.58) - road_shift, int(height * 0.70)),
        (78, 74, 70),
        max(10, width // 45),
        cv2.LINE_AA,
    )
    for offset in range(-width, width * 2, 170):
        x0 = offset - road_shift
        cv2.line(
            canvas,
            (x0, height),
            (x0 + int(width * 0.10), int(height * 0.82)),
            (218, 214, 188),
            max(2, width // 330),
            cv2.LINE_AA,
        )

    for index in range(11):
        base_x = int((index * 137 + 40 - t * (18 + index)) % (width + 120)) - 60
        tree_h = 70 + (index * 19) % 64
        tree_x = base_x
        tree_y = int(height * 0.65) - (index % 3) * 12
        cv2.rectangle(
            canvas,
            (tree_x - 3, tree_y - tree_h // 3),
            (tree_x + 3, tree_y + 10),
            (86, 66, 40),
            -1,
        )
        cv2.circle(
            canvas, (tree_x, tree_y - tree_h // 2), tree_h // 3,
            (50 + index * 3, 102 + index * 2, 55), -1, cv2.LINE_AA,
        )

    car_x = int(width * (0.18 + 0.52 * ((t * 0.11) % 1.0)))
    car_y = int(height * 0.80)
    car_w = max(84, width // 11)
    car_h = max(30, height // 18)
    cv2.rectangle(
        canvas,
        (car_x, car_y - car_h),
        (car_x + car_w, car_y),
        (194, 53, 45),
        -1,
        cv2.LINE_AA,
    )
    cv2.rectangle(
        canvas,
        (car_x + car_w // 4, car_y - car_h - car_h // 2),
        (car_x + car_w * 3 // 4, car_y - car_h + 3),
        (220, 226, 228),
        -1,
        cv2.LINE_AA,
    )
    for wheel_x in (car_x + car_w // 5, car_x + car_w * 4 // 5):
        cv2.circle(canvas, (wheel_x, car_y + 3), 9, (20, 20, 20), -1, cv2.LINE_AA)

    rng = np.random.default_rng(1000 + frame)
    grain = rng.normal(0.0, 2.2, (height, width, 1)).astype(np.float32)
    yy, xx = np.mgrid[0:height, 0:width]
    vignette = 1.0 - 0.15 * (
        ((xx - width / 2) / (width / 2)) ** 2
        + ((yy - height / 2) / (height / 2)) ** 2
    )
    canvas = np.clip(canvas.astype(np.float32) * vignette[..., None] + grain, 0, 255)
    return canvas.astype(np.uint8)


def _active_geometry(layout: str, width: int, height: int) -> tuple[int, int, int, int]:
    if layout == "full":
        return 0, 0, width, height
    if layout == "letterbox":
        active_height = int(round(width / 2.39))
        top = (height - active_height) // 2
        return 0, top, width, active_height
    if layout == "pillarbox":
        active_width = int(round(height * 4 / 3))
        left = (width - active_width) // 2
        return left, 0, active_width, height
    if layout == "fourside":
        active_width = int(round(width * 0.72))
        active_height = int(round(height * 0.72))
        return (
            (width - active_width) // 2,
            (height - active_height) // 2,
            active_width,
            active_height,
        )
    raise ValueError(f"unsupported layout {layout}")


def _compose_frame(
    spec: ClipSpec, frame_index: int, width: int, height: int
) -> np.ndarray:
    if spec.black_start <= frame_index <= spec.black_end:
        return np.zeros((height, width, 3), dtype=np.uint8)
    layout = spec.initial_layout if frame_index < spec.switch_frame else "letterbox"
    x, y, active_width, active_height = _active_geometry(layout, width, height)
    source = _base_scene(width, height, frame_index, spec.fps)
    if layout == "letterbox":
        source_top = (height - active_height) // 2
        active = source[source_top : source_top + active_height, :]
    elif layout == "pillarbox":
        source_left = (width - active_width) // 2
        active = source[:, source_left : source_left + active_width]
    else:
        active = source[y : y + active_height, x : x + active_width]
    if active.shape[:2] != (active_height, active_width):
        active = cv2.resize(
            active, (active_width, active_height), interpolation=cv2.INTER_AREA
        )
    canvas = np.zeros((height, width, 3), dtype=np.uint8)
    canvas[y : y + active_height, x : x + active_width] = active

    label = f"{spec.name.upper()}  F{frame_index:04d}  {frame_index / spec.fps:06.2f}s"
    cv2.rectangle(
        canvas, (x + 18, y + 16), (x + 355, y + 50), (12, 16, 18), -1
    )
    cv2.putText(
        canvas, label, (x + 28, y + 41), cv2.FONT_HERSHEY_SIMPLEX,
        0.58, (238, 240, 236), 1, cv2.LINE_AA,
    )
    if (
        spec.subtitle_start <= frame_index <= spec.subtitle_end
        and layout == "letterbox"
    ):
        text = "SUBTITLE ENTERS THE BLACK BAR"
        scale = 0.82
        thickness = 2
        (text_width, text_height), baseline = cv2.getTextSize(
            text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness
        )
        text_x = (width - text_width) // 2
        text_y = height - max(22, (height - (y + active_height)) // 2 + text_height // 2)
        cv2.putText(
            canvas, text, (text_x, text_y), cv2.FONT_HERSHEY_SIMPLEX,
            scale, (245, 242, 225), thickness, cv2.LINE_AA,
        )
    if frame_index < spec.fade_frames:
        progress = frame_index / max(1, spec.fade_frames - 1)
        factor = progress ** 1.7
        canvas = np.clip(canvas.astype(np.float32) * factor, 0, 255).astype(np.uint8)
    return canvas


def _truth_for(spec: ClipSpec, width: int, height: int) -> dict:
    initial_rect = _active_geometry(spec.initial_layout, width, height)
    letter_rect = _active_geometry("letterbox", width, height)
    initial_aspect = initial_rect[2] / initial_rect[3]
    letter_aspect = letter_rect[2] / letter_rect[3]
    return {
        "clip": spec.name,
        "description": "Deterministic real-shot-style synthetic audit fixture",
        "canvas": {"width": width, "height": height, "fps": spec.fps},
        "frame_count": spec.frame_count,
        "switch_frames": [spec.switch_frame],
        "segments": [
            {
                "start_frame": 0,
                "end_frame": spec.black_start - 1,
                "layout": spec.initial_layout,
                "aspect": round(initial_aspect, 6),
                "rect": {
                    "x": initial_rect[0], "y": initial_rect[1],
                    "width": initial_rect[2], "height": initial_rect[3],
                },
            },
            {
                "start_frame": spec.black_start,
                "end_frame": spec.black_end,
                "layout": "black",
                "aspect": None,
                "rect": None,
            },
            {
                "start_frame": spec.switch_frame,
                "end_frame": spec.frame_count - 1,
                "layout": "letterbox",
                "aspect": round(letter_aspect, 6),
                "rect": {
                    "x": letter_rect[0], "y": letter_rect[1],
                    "width": letter_rect[2], "height": letter_rect[3],
                },
            },
        ],
        "events": {
            "fade_in": {"start_frame": 0, "end_frame": spec.fade_frames - 1},
            "full_black": {
                "start_frame": spec.black_start, "end_frame": spec.black_end
            },
            "subtitle_in_black_bar": {
                "start_frame": spec.subtitle_start,
                "end_frame": spec.subtitle_end,
                "edge": "bottom",
            },
        },
    }


def _declared_rows(spec: ClipSpec) -> list[dict]:
    if spec.name == "long":
        return [
            {
                "start_frame": 0, "end_frame": 83, "aspect": "16:9",
                "note": "editor declared 16:9 through 3.5s",
            },
            {
                "start_frame": 84, "end_frame": 239, "aspect": "2.39:1",
                "note": "editor declared scope from 3.5s",
            },
            {
                "start_frame": 240, "end_frame": spec.frame_count - 1,
                "aspect": "16:9", "note": "deliberate tail declaration error",
            },
        ]
    return [
        {
            "start_frame": 0, "end_frame": 71, "aspect": "4:3",
            "note": "editor declared 4:3 through 3.0s",
        },
        {
            "start_frame": 72, "end_frame": spec.frame_count - 1,
            "aspect": "2.39:1", "note": "deliberate six-frame late switch",
        },
    ]


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def encode_clip(
    spec: ClipSpec,
    output: Path,
    width: int = 1280,
    height: int = 720,
) -> dict:
    ffmpeg = find_ffmpeg()
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg, "-y", "-v", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{width}x{height}", "-r", str(spec.fps), "-i", "pipe:0",
        "-an", "-c:v", "libx264", "-preset", "medium", "-crf", str(spec.crf),
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output),
    ]
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    assert proc.stdin is not None
    try:
        for frame_index in range(spec.frame_count):
            frame = _compose_frame(spec, frame_index, width, height)
            proc.stdin.write(frame.tobytes())
    finally:
        proc.stdin.close()
        stderr = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        if proc.stderr:
            proc.stderr.close()
        code = proc.wait()
    if code:
        raise RuntimeError(f"ffmpeg encode failed ({code}): {stderr[-2000:]}")
    return {"path": str(output), "frames": spec.frame_count}


def generate_materials(output_dir: str | Path) -> dict:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    manifest: dict = {"clips": {}}
    for name, spec in CLIPS.items():
        video_path = output / f"{name}_master.mp4"
        truth_path = output / f"{name}_truth.json"
        declared_path = output / f"{name}_declared.csv"
        encoded = encode_clip(spec, video_path)
        truth = _truth_for(spec, 1280, 720)
        _write_json(truth_path, truth)
        with declared_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=["start_frame", "end_frame", "aspect", "note"]
            )
            writer.writeheader()
            writer.writerows(_declared_rows(spec))
        manifest["clips"][name] = {
            **encoded,
            "duration": spec.duration,
            "fps": spec.fps,
            "truth": str(truth_path),
            "declared_schedule": str(declared_path),
            "switch_frame": spec.switch_frame,
        }
    _write_json(output / "manifest.json", manifest)
    return manifest
