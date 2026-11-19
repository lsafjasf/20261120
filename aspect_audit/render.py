from __future__ import annotations

import subprocess
from pathlib import Path

import cv2
import numpy as np

from .media import find_ffmpeg, iter_rgb_frames


LAYOUT_COLORS = {
    "full": (120, 220, 77),
    "letterbox": (67, 186, 255),
    "pillarbox": (255, 157, 69),
    "fourside": (255, 116, 191),
    "black": (76, 88, 245),
    "fade": (175, 180, 188),
    "unknown": (210, 210, 210),
}


def _label(
    canvas: np.ndarray,
    text: str,
    origin: tuple[int, int],
    color: tuple[int, int, int],
    scale: float = 0.58,
) -> None:
    x, y = origin
    font = cv2.FONT_HERSHEY_SIMPLEX
    (width, height), baseline = cv2.getTextSize(text, font, scale, 1)
    cv2.rectangle(
        canvas,
        (x, y),
        (x + width + 16, y + height + baseline + 12),
        (13, 17, 21),
        -1,
    )
    cv2.putText(
        canvas, text, (x + 8, y + height + 5), font, scale,
        color, 1, cv2.LINE_AA,
    )


def render_review(
    source: str,
    analysis: dict,
    output: str | Path,
    comparison: dict | None = None,
) -> str:
    video = analysis["video"]
    width = int(video["width"])
    height = int(video["height"])
    fps = float(video["fps"])
    frames = analysis["frames"]
    mismatch_frames: set[int] = set()
    if comparison:
        for item in comparison.get("mismatches", []):
            mismatch_frames.update(
                range(int(item["start_frame"]), int(item["end_frame"]) + 1)
            )
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        find_ffmpeg(), "-y", "-v", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{width}x{height}", "-r", f"{fps}", "-i", "pipe:0",
        "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output_path),
    ]
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    assert proc.stdin is not None
    try:
        for index, rgb in enumerate(iter_rgb_frames(source, width, height)):
            frame = frames[index]
            canvas = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            color = LAYOUT_COLORS.get(frame.layout, (255, 255, 255))
            rect = frame.rect
            cv2.rectangle(
                canvas,
                (rect.x, rect.y),
                (rect.x + rect.width - 1, rect.y + rect.height - 1),
                color,
                3,
                cv2.LINE_AA,
            )
            aspect = f"{frame.aspect:.3f}:1" if frame.layout not in ("black", "fade") else "n/a"
            text = (
                f"{frame.layout.upper()}  {aspect}  "
                f"F{frame.frame}  conf {frame.confidence:.2f}"
            )
            label_y = rect.y + 10
            if label_y + 42 > height:
                label_y = max(8, rect.y - 52)
            _label(canvas, text, (max(8, rect.x + 8), label_y), color)
            if frame.subtitle_edges:
                subtitle_text = (
                    f"SUBTITLE IN {frame.subtitle_edges[0].upper()} BAR  "
                    f"conf {frame.subtitle_confidence:.2f}"
                )
                _label(
                    canvas, subtitle_text,
                    (max(8, rect.x + 8), max(8, height - 48)),
                    (220, 210, 69),
                    0.50,
                )
            if frame.frame in mismatch_frames:
                cv2.rectangle(
                    canvas, (4, 4), (width - 5, height - 5),
                    (58, 62, 245), 5, cv2.LINE_AA,
                )
                _label(
                    canvas, "DECLARED ASPECT MISMATCH",
                    (max(8, width - 340), 10),
                    (94, 100, 255),
                    0.52,
                )
            proc.stdin.write(
                cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB).tobytes()
            )
    finally:
        proc.stdin.close()
        stderr = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        if proc.stderr:
            proc.stderr.close()
        code = proc.wait()
    if code:
        raise RuntimeError(f"review encode failed ({code}): {stderr[-2000:]}")
    return str(output_path)
