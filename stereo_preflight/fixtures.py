from __future__ import annotations

import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Callable

from .media import pack_eyes
from .pattern import EyeSpec, render_eye


@dataclass(frozen=True)
class TestPattern:
    packing: str = "sbs"
    eye_width: int = 720
    eye_height: int = 540
    frames: int = 48
    frame_rate: int | Fraction = 24
    swap_eyes: bool = False
    drop_second_at: int | None = None
    second_frame_shift: int = 0
    foreground_disparity: float = 12.0
    background_disparity: float = -6.0
    vertical_offset: float = 0.0

    @property
    def width(self) -> int:
        return self.eye_width * 2 if self.packing == "sbs" else self.eye_width

    @property
    def height(self) -> int:
        return self.eye_height if self.packing == "sbs" else self.eye_height * 2


def _eye_specs(pattern: TestPattern, index: int) -> tuple[EyeSpec, EyeSpec]:
    second_id = index + pattern.second_frame_shift
    if pattern.drop_second_at is not None and index >= pattern.drop_second_at:
        second_id += 1
    left = EyeSpec(
        label="L",
        frame_id=index,
        foreground_dx=-pattern.foreground_disparity / 2,
        foreground_dy=-pattern.vertical_offset / 2,
        background_dx=-pattern.background_disparity / 2,
        background_dy=-pattern.vertical_offset / 2,
    )
    right = EyeSpec(
        label="R",
        frame_id=second_id,
        foreground_dx=pattern.foreground_disparity / 2,
        foreground_dy=pattern.vertical_offset / 2,
        background_dx=pattern.background_disparity / 2,
        background_dy=pattern.vertical_offset / 2,
    )
    return (right, left) if pattern.swap_eyes else (left, right)


def render_packed_frame(pattern: TestPattern, index: int) -> bytes:
    first, second = _eye_specs(pattern, index)
    first_data = render_eye(pattern.eye_width, pattern.eye_height, first)
    second_data = render_eye(pattern.eye_width, pattern.eye_height, second)
    return pack_eyes(
        first_data,
        second_data,
        pattern.width,
        pattern.height,
        pattern.packing,
    )


def write_test_video(
    output: str | Path,
    pattern: TestPattern,
    *,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    destination = Path(output).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{pattern.width}x{pattern.height}",
        "-framerate",
        str(pattern.frame_rate),
        "-i",
        "-",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "10",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(destination),
    ]
    proc = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    assert proc.stdin is not None
    try:
        for index in range(pattern.frames):
            proc.stdin.write(render_packed_frame(pattern, index))
            if progress:
                progress(index + 1, pattern.frames)
        proc.stdin.close()
        assert proc.stderr is not None
        error = proc.stderr.read().decode("utf-8", "replace").strip()
        proc.stderr.close()
        if proc.stdin:
            proc.stdin.close()
        return_code = proc.wait()
        if return_code:
            raise RuntimeError(error or f"ffmpeg exited with {return_code}")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    return destination
