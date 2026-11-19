from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .core import (
    StabilizationConfig,
    _read_video,
    estimate_frame_pair,
    gaussian_smooth,
)


@dataclass
class MotionMeasurement:
    displacement_x: np.ndarray
    displacement_y: np.ndarray
    angle_deg: np.ndarray
    jitter_residual_x: np.ndarray
    jitter_residual_y: np.ndarray
    rotation_residual_deg: np.ndarray
    jitter_rms_px: float
    jitter_rms_deg: float
    motion_rms_px: float
    rotation_rms_deg: float
    tracked_points_mean: float
    inlier_ratio_mean: float


def _center_motion(matrix: np.ndarray, center: tuple[float, float]) -> tuple[float, float, float]:
    cx, cy = center
    mapped = matrix[:2, :2] @ np.array([cx, cy]) + matrix[:2, 2]
    angle = float(np.degrees(np.arctan2(matrix[1, 0], matrix[0, 0])))
    return float(mapped[0] - cx), float(mapped[1] - cy), angle


def measure_video_motion(
    path: str | Path,
    smoothing_sigma: float = 9.0,
    config: StabilizationConfig | None = None,
) -> MotionMeasurement:
    config = config or StabilizationConfig()
    frames, _, width, height = _read_video(path)
    gray = [cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) for frame in frames]
    center = (width / 2.0, height / 2.0)
    dx = np.zeros(len(frames), dtype=np.float64)
    dy = np.zeros(len(frames), dtype=np.float64)
    angle = np.zeros(len(frames), dtype=np.float64)
    tracked = np.zeros(len(frames), dtype=np.float64)
    inliers = np.zeros(len(frames), dtype=np.float64)
    for index in range(1, len(frames)):
        estimate = estimate_frame_pair(gray[index - 1], gray[index], config)
        dx[index], dy[index], angle[index] = _center_motion(estimate.matrix, center)
        tracked[index] = estimate.tracked_points
        inliers[index] = estimate.inlier_ratio
    angle = np.unwrap(np.radians(angle))

    residual_x = dx - gaussian_smooth(dx, smoothing_sigma)
    residual_y = dy - gaussian_smooth(dy, smoothing_sigma)
    residual_angle = angle - gaussian_smooth(angle, smoothing_sigma)
    return MotionMeasurement(
        displacement_x=dx,
        displacement_y=dy,
        angle_deg=np.degrees(angle),
        jitter_residual_x=residual_x,
        jitter_residual_y=residual_y,
        rotation_residual_deg=np.degrees(residual_angle),
        jitter_rms_px=float(np.sqrt(np.mean(residual_x**2 + residual_y**2))),
        jitter_rms_deg=float(np.degrees(np.sqrt(np.mean(residual_angle**2)))),
        motion_rms_px=float(np.sqrt(np.mean(dx[1:] ** 2 + dy[1:] ** 2))),
        rotation_rms_deg=float(np.degrees(np.sqrt(np.mean(angle[1:] ** 2)))),
        tracked_points_mean=float(np.mean(tracked[1:])),
        inlier_ratio_mean=float(np.mean(inliers[1:])),
    )


def perspective_and_shear_error(matrices: np.ndarray) -> tuple[float, float]:
    shear = []
    perspective = []
    for matrix in matrices:
        scale = float(np.hypot(matrix[0, 0], matrix[1, 0]))
        shear.append(
            (abs(matrix[0, 0] - matrix[1, 1]) + abs(matrix[0, 1] + matrix[1, 0]))
            / max(scale, 1e-12)
        )
        perspective.append(
            max(abs(matrix[2, 0]), abs(matrix[2, 1]), abs(matrix[2, 2] - 1.0))
        )
    return float(max(shear)), float(max(perspective))


def border_zero_ratio(path: str | Path, border: int = 2) -> float:
    frames, _, _, _ = _read_video(path)
    ratios = []
    for frame in frames:
        edge = np.zeros(frame.shape[:2], dtype=bool)
        edge[:border, :] = True
        edge[-border:, :] = True
        edge[:, :border] = True
        edge[:, -border:] = True
        ratios.append(float(np.mean(np.all(frame[edge] == 0, axis=1))))
    return float(max(ratios))


def solid_black_border_lines(path: str | Path, border: int = 2) -> dict[str, float]:
    """Detect coherent black bands rather than isolated dark image content."""
    frames, _, _, _ = _read_video(path)
    maximum_line_fraction = 0.0
    affected_frames = 0
    for frame in frames:
        black = np.all(frame < 5, axis=2)
        candidates = [
            black[:border, :].mean(axis=1),
            black[-border:, :].mean(axis=1),
            black[:, :border].mean(axis=0),
            black[:, -border:].mean(axis=0),
        ]
        frame_maximum = max(float(np.max(values)) for values in candidates)
        maximum_line_fraction = max(maximum_line_fraction, frame_maximum)
        if frame_maximum >= 0.9:
            affected_frames += 1
    return {
        "solid_black_line_fraction_max": maximum_line_fraction,
        "affected_frames": float(affected_frames),
    }


def region_line_metrics(
    frame: np.ndarray, bbox: tuple[float, float, float, float]
) -> dict[str, float]:
    x, y, width, height = bbox
    height_px, width_px = frame.shape[:2]
    left = max(0, min(width_px - 2, int(round(x))))
    top = max(0, min(height_px - 2, int(round(y))))
    right = max(left + 2, min(width_px, int(round(x + width))))
    bottom = max(top + 2, min(height_px, int(round(y + height))))
    gray = cv2.cvtColor(frame[top:bottom, left:right], cv2.COLOR_BGR2GRAY)
    low, high = np.percentile(gray, [1.0, 99.0])
    laplacian = cv2.Laplacian(gray, cv2.CV_32F)
    edges = cv2.Canny(gray, 40, 120)
    return {
        "contrast_p99_p01": float(high - low),
        "dark_pixel_ratio": float(np.mean(gray < 128)),
        "edge_pixel_ratio": float(np.mean(edges > 0)),
        "laplacian_energy": float(np.mean(np.abs(laplacian))),
        "mean_luma": float(np.mean(gray)),
    }
