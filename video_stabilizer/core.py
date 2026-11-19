from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np


@dataclass(frozen=True)
class StabilizationConfig:
    smoothing_sigma: float = 9.0
    target_crop_linear: float = 0.88
    feature_count: int = 700
    feature_quality: float = 0.008
    feature_min_distance: float = 7.0
    fb_error_px: float = 1.25
    ransac_threshold_px: float = 2.0
    crop_margin_px: float = 2.0
    interpolation: int = cv2.INTER_LANCZOS4


@dataclass
class FramePairEstimate:
    matrix: np.ndarray
    tracked_points: int
    inlier_points: int
    inlier_ratio: float
    fallback: bool = False


@dataclass
class StabilizationResult:
    output_path: str
    matrices_path: str
    width: int
    height: int
    crop_width: int
    crop_height: int
    crop_linear_ratio: float
    crop_area_ratio: float
    frame_count: int
    fps: float
    motion_matrices: np.ndarray
    camera_poses: np.ndarray
    stabilization_matrices: np.ndarray
    output_matrices: np.ndarray
    crop_linear_ratios: np.ndarray
    valid_mask_min: np.ndarray
    black_border_ratios: np.ndarray
    feature_counts: np.ndarray
    inlier_ratios: np.ndarray
    metadata: dict = field(default_factory=dict)


def _as_3x3(matrix: np.ndarray) -> np.ndarray:
    result = np.eye(3, dtype=np.float64)
    result[:2, :] = matrix[:2, :]
    return result


def _translation(tx: float, ty: float) -> np.ndarray:
    return np.array([[1.0, 0.0, tx], [0.0, 1.0, ty], [0.0, 0.0, 1.0]])


def _similarity(angle_rad: float, scale: float) -> np.ndarray:
    cosine = scale * np.cos(angle_rad)
    sine = scale * np.sin(angle_rad)
    return np.array(
        [[cosine, -sine, 0.0], [sine, cosine, 0.0], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )


def decompose_centered_similarity(
    matrix: np.ndarray, center: tuple[float, float]
) -> tuple[float, float, float, float]:
    """Return center displacement, rotation and uniform scale."""
    matrix = _as_3x3(matrix)
    cx, cy = center
    linear = matrix[:2, :2]
    angle = float(np.arctan2(linear[1, 0], linear[0, 0]))
    scale = float(np.hypot(linear[0, 0], linear[1, 0]))
    mapped_center = linear @ np.array([cx, cy]) + matrix[:2, 2]
    dx = float(mapped_center[0] - cx)
    dy = float(mapped_center[1] - cy)
    return dx, dy, angle, scale


def compose_centered_similarity(
    dx: float,
    dy: float,
    angle_rad: float,
    scale: float,
    center: tuple[float, float],
) -> np.ndarray:
    cx, cy = center
    return (
        _translation(cx + dx, cy + dy)
        @ _similarity(angle_rad, scale)
        @ _translation(-cx, -cy)
    )


def gaussian_smooth(values: np.ndarray, sigma: float) -> np.ndarray:
    values = np.asarray(values, dtype=np.float64)
    if sigma <= 0 or values.size < 2:
        return values.copy()
    radius = max(1, int(round(3.0 * sigma)))
    offsets = np.arange(-radius, radius + 1, dtype=np.float64)
    kernel = np.exp(-0.5 * (offsets / sigma) ** 2)
    kernel /= kernel.sum()
    pad = radius
    padded = np.pad(values, (pad, pad), mode="reflect")
    return np.convolve(padded, kernel, mode="valid")


def safe_crop_linear_ratio(
    matrix: np.ndarray,
    width: int,
    height: int,
    margin_px: float,
) -> float:
    """Largest centered, same-aspect crop fully covered by the source."""
    inverse = np.linalg.inv(_as_3x3(matrix))
    center = np.array([width / 2.0, height / 2.0])
    max_ratio = min(
        max(0.0, 1.0 - 2.0 * margin_px / width),
        max(0.0, 1.0 - 2.0 * margin_px / height),
    )

    def fits(ratio: float) -> bool:
        half_width = ratio * width / 2.0
        half_height = ratio * height / 2.0
        corners = np.array(
            [
                [center[0] - half_width, center[1] - half_height, 1.0],
                [center[0] + half_width, center[1] - half_height, 1.0],
                [center[0] + half_width, center[1] + half_height, 1.0],
                [center[0] - half_width, center[1] + half_height, 1.0],
            ]
        )
        mapped = (inverse @ corners.T).T[:, :2]
        return bool(
            np.all(mapped[:, 0] >= margin_px)
            and np.all(mapped[:, 0] <= width - 1.0 - margin_px)
            and np.all(mapped[:, 1] >= margin_px)
            and np.all(mapped[:, 1] <= height - 1.0 - margin_px)
        )

    if fits(max_ratio):
        return float(max_ratio)
    low, high = 0.0, max_ratio
    for _ in range(40):
        middle = 0.5 * (low + high)
        if fits(middle):
            low = middle
        else:
            high = middle
    return float(low)


def build_output_matrix(
    stabilized: np.ndarray,
    width: int,
    height: int,
    crop_linear_ratio: float,
) -> tuple[np.ndarray, int, int]:
    crop_width = max(2.0, width * crop_linear_ratio)
    crop_height = max(2.0, height * crop_linear_ratio)
    scale = width / crop_width
    crop_left = 0.5 * (width - crop_width)
    crop_top = 0.5 * (height - crop_height)
    crop_to_output = np.array(
        [
            [scale, 0.0, -crop_left * scale],
            [0.0, scale, -crop_top * scale],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    return (
        _as_3x3(crop_to_output @ stabilized),
        int(round(crop_width)),
        int(round(crop_height)),
    )


def estimate_frame_pair(
    previous_gray: np.ndarray,
    current_gray: np.ndarray,
    config: StabilizationConfig,
) -> FramePairEstimate:
    height, width = previous_gray.shape
    border = 8
    mask = np.zeros_like(previous_gray)
    mask[border : height - border, border : width - border] = 255
    previous_points = cv2.goodFeaturesToTrack(
        previous_gray,
        maxCorners=config.feature_count,
        qualityLevel=config.feature_quality,
        minDistance=config.feature_min_distance,
        mask=mask,
        blockSize=5,
        useHarrisDetector=False,
    )
    if previous_points is None or len(previous_points) < 8:
        return FramePairEstimate(np.eye(3), 0, 0, 0.0, fallback=True)

    lk_parameters = dict(
        winSize=(21, 21),
        maxLevel=4,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 40, 0.01),
    )
    current_points, status_forward, _ = cv2.calcOpticalFlowPyrLK(
        previous_gray, current_gray, previous_points, None, **lk_parameters
    )
    if current_points is None:
        return FramePairEstimate(np.eye(3), 0, 0, 0.0, fallback=True)
    back_points, status_backward, _ = cv2.calcOpticalFlowPyrLK(
        current_gray, previous_gray, current_points, None, **lk_parameters
    )
    if back_points is None:
        return FramePairEstimate(np.eye(3), 0, 0, 0.0, fallback=True)

    forward = previous_points.reshape(-1, 2)
    matched = current_points.reshape(-1, 2)
    backward = back_points.reshape(-1, 2)
    forward_backward_error = np.linalg.norm(forward - backward, axis=1)
    valid = (
        status_forward.reshape(-1).astype(bool)
        & status_backward.reshape(-1).astype(bool)
        & (forward_backward_error <= config.fb_error_px)
    )
    if valid.sum() < 8:
        return FramePairEstimate(np.eye(3), int(valid.sum()), 0, 0.0, fallback=True)

    matrix, inliers = cv2.estimateAffinePartial2D(
        forward[valid],
        matched[valid],
        method=cv2.RANSAC,
        ransacReprojThreshold=config.ransac_threshold_px,
        maxIters=2500,
        confidence=0.999,
        refineIters=20,
    )
    if matrix is None or inliers is None:
        displacement = np.median(matched[valid] - forward[valid], axis=0)
        fallback = _translation(float(displacement[0]), float(displacement[1]))
        return FramePairEstimate(fallback, int(valid.sum()), 0, 0.0, fallback=True)

    inlier_count = int(inliers.sum())
    if inlier_count < 8:
        return FramePairEstimate(np.eye(3), int(valid.sum()), inlier_count, 0.0, True)
    matrix_3x3 = _as_3x3(matrix)
    linear = matrix_3x3[:2, :2]
    scale = float(np.hypot(linear[0, 0], linear[1, 0]))
    determinant = float(np.linalg.det(linear))
    if not np.isfinite(matrix_3x3).all() or not 0.94 <= scale <= 1.06 or determinant <= 0:
        displacement = np.median(matched[valid] - forward[valid], axis=0)
        fallback = _translation(float(displacement[0]), float(displacement[1]))
        return FramePairEstimate(fallback, int(valid.sum()), inlier_count, 0.0, True)
    return FramePairEstimate(
        matrix_3x3,
        tracked_points=int(valid.sum()),
        inlier_points=inlier_count,
        inlier_ratio=float(inlier_count / max(1, valid.sum())),
    )


def _read_video(path: str | Path) -> tuple[list[np.ndarray], float, int, int]:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise FileNotFoundError(f"cannot open video: {path}")
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    frames: list[np.ndarray] = []
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        frames.append(frame)
    capture.release()
    if not frames:
        raise ValueError(f"no frames decoded from: {path}")
    height, width = frames[0].shape[:2]
    return frames, fps if fps > 0 else 30.0, width, height


def _writer(path: str | Path, fps: float, width: int, height: int) -> cv2.VideoWriter:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    codec = "FFV1" if Path(path).suffix.lower() == ".avi" else "mp4v"
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*codec), fps, (width, height)
    )
    if not writer.isOpened():
        raise RuntimeError(f"cannot create video writer: {path}")
    return writer


def _write_matrices(
    path: str | Path,
    motion: np.ndarray,
    poses: np.ndarray,
    stabilization: np.ndarray,
    output: np.ndarray,
    crop_ratios: np.ndarray,
    valid_min: np.ndarray,
    black_ratios: np.ndarray,
    features: np.ndarray,
    inliers: np.ndarray,
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    csv_path = path.with_suffix(".csv")
    np.savez_compressed(
        path.with_suffix(".npz"),
        motion_matrices=motion,
        camera_poses=poses,
        stabilization_matrices=stabilization,
        output_matrices=output,
        crop_linear_ratios=crop_ratios,
        valid_mask_min=valid_min,
        black_border_ratios=black_ratios,
        tracked_points=features,
        inlier_ratios=inliers,
    )
    flat = np.concatenate(
        [
            motion.reshape(len(motion), 9),
            poses.reshape(len(poses), 9),
            stabilization.reshape(len(stabilization), 9),
            output.reshape(len(output), 9),
            crop_ratios[:, None],
            valid_min[:, None],
            black_ratios[:, None],
            features[:, None],
            inliers[:, None],
        ],
        axis=1,
    )
    header = ",".join(
        [f"motion_{index}" for index in range(9)]
        + [f"pose_{index}" for index in range(9)]
        + [f"stabilization_{index}" for index in range(9)]
        + [f"output_{index}" for index in range(9)]
        + ["crop_linear_ratio", "valid_mask_min", "black_border_ratio"]
        + ["tracked_points", "inlier_ratio"]
    )
    np.savetxt(
        csv_path, flat, delimiter=",", header=header, comments="", fmt="%.10g"
    )


def _mean(values: Iterable[float]) -> float:
    array = np.asarray(list(values), dtype=np.float64)
    return float(array.mean()) if array.size else 0.0


def stabilize_video(
    input_path: str | Path,
    output_path: str | Path,
    matrices_path: str | Path,
    config: StabilizationConfig | None = None,
) -> StabilizationResult:
    config = config or StabilizationConfig()
    frames, fps, width, height = _read_video(input_path)
    count = len(frames)
    gray_frames = [cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) for frame in frames]
    center = (width / 2.0, height / 2.0)

    motion = np.repeat(np.eye(3)[None, :, :], count, axis=0)
    poses = np.repeat(np.eye(3)[None, :, :], count, axis=0)
    tracked = np.zeros(count, dtype=np.int32)
    inlier_ratios = np.zeros(count, dtype=np.float64)
    for index in range(1, count):
        estimate = estimate_frame_pair(gray_frames[index - 1], gray_frames[index], config)
        motion[index] = estimate.matrix
        poses[index] = estimate.matrix @ poses[index - 1]
        tracked[index] = estimate.tracked_points
        inlier_ratios[index] = estimate.inlier_ratio

    trajectory = np.array(
        [decompose_centered_similarity(matrix, center) for matrix in poses],
        dtype=np.float64,
    )
    trajectory[:, 2] = np.unwrap(trajectory[:, 2])
    smooth = np.empty_like(trajectory)
    for column in range(4):
        smooth[:, column] = gaussian_smooth(trajectory[:, column], config.smoothing_sigma)
    smooth_poses = np.array(
        [
            compose_centered_similarity(dx, dy, angle, scale, center)
            for dx, dy, angle, scale in smooth
        ]
    )
    raw_stabilization = np.array(
        [smooth_poses[index] @ np.linalg.inv(poses[index]) for index in range(count)]
    )
    stabilized_full = raw_stabilization

    safe_ratios = np.array(
        [
            safe_crop_linear_ratio(matrix, width, height, config.crop_margin_px)
            for matrix in stabilized_full
        ]
    )
    crop_linear = min(config.target_crop_linear, float(np.min(safe_ratios)))
    output_matrices: np.ndarray
    crop_width = crop_height = 0
    valid_min = np.zeros(count, dtype=np.float64)
    black_ratios = np.ones(count, dtype=np.float64)

    for _ in range(12):
        built = [
            build_output_matrix(matrix, width, height, crop_linear)
            for matrix in stabilized_full
        ]
        output_matrices = np.array([item[0] for item in built])
        crop_width, crop_height = built[0][1], built[0][2]
        ones = np.full((height, width), 255, dtype=np.uint8)
        masks = [
            cv2.warpAffine(
                ones,
                matrix[:2],
                (width, height),
                flags=cv2.INTER_NEAREST,
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=0,
            )
            for matrix in output_matrices
        ]
        valid_min = np.array([float(mask.min()) for mask in masks])
        black_ratios = np.array([float(np.mean(mask < 255)) for mask in masks])
        if np.all(valid_min >= 255):
            break
        crop_linear *= 0.99
    else:
        raise RuntimeError("failed to find a black-border-free crop")

    writer = _writer(output_path, fps, width, height)
    try:
        for frame, matrix in zip(frames, output_matrices):
            writer.write(
                cv2.warpAffine(
                    frame,
                    matrix[:2],
                    (width, height),
                    flags=config.interpolation,
                    borderMode=cv2.BORDER_CONSTANT,
                    borderValue=(0, 0, 0),
                )
            )
    finally:
        writer.release()

    crop_ratios = np.full(count, crop_linear, dtype=np.float64)
    _write_matrices(
        matrices_path,
        motion,
        poses,
        raw_stabilization,
        output_matrices,
        crop_ratios,
        valid_min,
        black_ratios,
        tracked.astype(np.float64),
        inlier_ratios,
    )
    metadata = {
        "input": str(input_path),
        "output": str(output_path),
        "smoothing_sigma": config.smoothing_sigma,
        "target_crop_linear": config.target_crop_linear,
        "safe_crop_min": float(np.min(safe_ratios)),
        "mean_tracked_points": _mean(tracked[1:]),
        "mean_inlier_ratio": _mean(inlier_ratios[1:]),
        "fallback_frames": int(np.sum(inlier_ratios[1:] == 0.0)),
    }
    return StabilizationResult(
        output_path=str(output_path),
        matrices_path=str(Path(matrices_path).with_suffix(".npz")),
        width=width,
        height=height,
        crop_width=crop_width,
        crop_height=crop_height,
        crop_linear_ratio=float(crop_linear),
        crop_area_ratio=float(crop_linear**2),
        frame_count=count,
        fps=fps,
        motion_matrices=motion,
        camera_poses=poses,
        stabilization_matrices=raw_stabilization,
        output_matrices=output_matrices,
        crop_linear_ratios=crop_ratios,
        valid_mask_min=valid_min,
        black_border_ratios=black_ratios,
        feature_counts=tracked,
        inlier_ratios=inlier_ratios,
        metadata=metadata,
    )
