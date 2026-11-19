from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .core import _read_video
from .metrics import MotionMeasurement


def _label(frame: np.ndarray, text: str, color: tuple[int, int, int]) -> np.ndarray:
    output = frame.copy()
    cv2.rectangle(output, (0, 0), (output.shape[1], 30), (12, 12, 12), -1)
    cv2.putText(
        output,
        text,
        (10, 21),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        color,
        1,
        cv2.LINE_AA,
    )
    return output


def representative_index(before: MotionMeasurement) -> int:
    magnitude = np.hypot(before.jitter_residual_x, before.jitter_residual_y)
    margin = min(10, max(1, len(magnitude) // 10))
    start = margin
    stop = max(start + 1, len(magnitude) - margin)
    return int(np.argmax(magnitude[start:stop]) + start)


def before_after_still(
    input_path: str | Path,
    output_path: str | Path,
    destination: str | Path,
    frame_index: int,
) -> str:
    before_frames, _, _, _ = _read_video(input_path)
    after_frames, _, _, _ = _read_video(output_path)
    index = min(frame_index, len(before_frames) - 1, len(after_frames) - 1)
    before = _label(before_frames[index], f"BEFORE  frame {index}", (70, 210, 255))
    after = _label(after_frames[index], f"AFTER / CROPPED  frame {index}", (95, 235, 120))
    montage = np.concatenate([before, after], axis=1)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(destination), montage)
    return str(destination)


def comparison_video(
    input_path: str | Path,
    output_path: str | Path,
    destination: str | Path,
) -> str:
    before_frames, fps, _, _ = _read_video(input_path)
    after_frames, _, _, _ = _read_video(output_path)
    height, width = before_frames[0].shape[:2]
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(destination),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (width * 2, height),
    )
    if not writer.isOpened():
        raise RuntimeError(f"cannot create comparison video: {destination}")
    try:
        for index, (before, after) in enumerate(zip(before_frames, after_frames)):
            left = _label(before, f"BEFORE  #{index:03d}", (70, 210, 255))
            right = _label(after, f"AFTER  #{index:03d}", (95, 235, 120))
            writer.write(np.concatenate([left, right], axis=1))
    finally:
        writer.release()
    return str(destination)


def _plot_panel(
    series: list[tuple[str, np.ndarray, tuple[int, int, int]]],
    title: str,
    y_label: str,
    width: int,
    height: int,
) -> np.ndarray:
    panel = np.full((height, width, 3), 250, dtype=np.uint8)
    left, right, top, bottom = 64, 20, 42, 46
    plot_width = width - left - right
    plot_height = height - top - bottom
    all_values = np.concatenate([values for _, values, _ in series])
    maximum = max(float(np.max(all_values)), 1e-6) * 1.08
    cv2.putText(
        panel,
        f"{title} [{y_label}]",
        (left, 26),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.62,
        (25, 25, 25),
        1,
        cv2.LINE_AA,
    )
    cv2.rectangle(
        panel,
        (left, top),
        (left + plot_width, top + plot_height),
        (185, 185, 185),
        1,
    )
    for step in range(5):
        value = maximum * step / 4.0
        row = top + plot_height - int(plot_height * step / 4.0)
        cv2.line(panel, (left, row), (left + plot_width, row), (225, 225, 225), 1)
        cv2.putText(
            panel,
            f"{value:5.2f}",
            (5, row + 5),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.4,
            (80, 80, 80),
            1,
            cv2.LINE_AA,
        )
    for name, values, color in series:
        points = []
        for index, value in enumerate(values):
            px = left + int(plot_width * index / max(1, len(values) - 1))
            py = top + plot_height - int(plot_height * min(value, maximum) / maximum)
            points.append((px, py))
        cv2.polylines(panel, [np.asarray(points)], False, color, 2, cv2.LINE_AA)
    legend_x = left
    for name, values, color in series:
        cv2.line(panel, (legend_x, height - 18), (legend_x + 24, height - 18), color, 3)
        cv2.putText(
            panel,
            name,
            (legend_x + 30, height - 13),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.42,
            (40, 40, 40),
            1,
            cv2.LINE_AA,
        )
        legend_x += 215
    return panel


def jitter_plot(
    before: MotionMeasurement,
    after: MotionMeasurement,
    destination: str | Path,
) -> str:
    before_magnitude = np.hypot(before.jitter_residual_x, before.jitter_residual_y)
    after_magnitude = np.hypot(after.jitter_residual_x, after.jitter_residual_y)
    top = _plot_panel(
        [
            ("before", before_magnitude, (35, 110, 230)),
            ("after", after_magnitude, (55, 170, 65)),
        ],
        "High-pass frame-motion residual (translation)",
        "px",
        1000,
        300,
    )
    bottom = _plot_panel(
        [
            ("before", np.abs(before.rotation_residual_deg), (35, 110, 230)),
            ("after", np.abs(after.rotation_residual_deg), (55, 170, 65)),
        ],
        "High-pass frame-motion residual (rotation)",
        "deg",
        1000,
        300,
    )
    canvas = np.concatenate([top, bottom], axis=0)
    cv2.putText(
        canvas,
        f"RMS px: {before.jitter_rms_px:.4f} -> {after.jitter_rms_px:.4f}",
        (650, 272),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.42,
        (40, 40, 40),
        1,
        cv2.LINE_AA,
    )
    cv2.putText(
        canvas,
        f"RMS deg: {before.jitter_rms_deg:.4f} -> {after.jitter_rms_deg:.4f}",
        (650, 572),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.42,
        (40, 40, 40),
        1,
        cv2.LINE_AA,
    )
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(destination), canvas)
    return str(destination)


def fine_line_panel(
    input_path: str | Path,
    output_path: str | Path,
    destination: str | Path,
    frame_index: int,
    before_metrics: dict[str, float],
    after_metrics: dict[str, float],
    zoom: int = 3,
) -> str:
    before_frames, _, _, _ = _read_video(input_path)
    after_frames, _, _, _ = _read_video(output_path)
    index = min(frame_index, len(before_frames) - 1, len(after_frames) - 1)
    height, width = before_frames[index].shape[:2]
    roi_width, roi_height = 280, 158
    left = (width - roi_width) // 2
    top = (height - roi_height) // 2
    before_roi = before_frames[index][
        top : top + roi_height, left : left + roi_width
    ]
    after_roi = after_frames[index][top : top + roi_height, left : left + roi_width]
    before_zoom = cv2.resize(
        before_roi,
        (roi_width * zoom, roi_height * zoom),
        interpolation=cv2.INTER_NEAREST,
    )
    after_zoom = cv2.resize(
        after_roi,
        (roi_width * zoom, roi_height * zoom),
        interpolation=cv2.INTER_NEAREST,
    )
    before_zoom = _label(
        before_zoom,
        f"BEFORE  contrast={before_metrics['contrast_p99_p01']:.1f}",
        (70, 210, 255),
    )
    after_zoom = _label(
        after_zoom,
        f"AFTER  contrast={after_metrics['contrast_p99_p01']:.1f}",
        (95, 235, 120),
    )
    canvas = np.concatenate([before_zoom, after_zoom], axis=1)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(destination), canvas)
    return str(destination)
