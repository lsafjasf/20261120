"""End-to-end frame metric pipeline: compute, analyse and persist."""

from __future__ import annotations

import csv
import json
import os
import time

import cv2
import numpy as np

from .metrics import (
    bt601_luma,
    msssim,
    msssim_map,
    psnr_global,
    psnr_map,
    ssim,
    ssim_map,
)

METRICS = ("psnr_db", "ssim", "msssim")
REGION_BLOCK = 16
HOTSPOT_PERCENTILE = 80.0


def list_reference_frames(frame_dir: str) -> list[str]:
    names = sorted(
        name for name in os.listdir(frame_dir) if name.endswith(".png")
    )
    return [os.path.join(frame_dir, name) for name in names]


def detect_noise_frames(
    frame_paths: list[str], high_segment_end: int
) -> tuple[np.ndarray, np.ndarray]:
    """Temporal high-pass energy on the reference luma planes.

    For each frame we take ``frame[t] - frame[t-1]`` and subtract its 5x5
    Gaussian smoothing: smooth object motion cancels out while pixel-wide
    noise bursts remain.  A robust median/MAD threshold inside the high
    bitrate segment marks the high-noise frames.
    """
    scores = np.zeros(len(frame_paths), dtype=np.float64)
    previous = None
    for idx, path in enumerate(frame_paths):
        gray = bt601_luma(cv2.imread(path, cv2.IMREAD_COLOR))
        if previous is not None:
            diff = gray - previous
            smooth = cv2.GaussianBlur(diff, (5, 5), 1.5)
            scores[idx] = float(np.mean(np.abs(diff - smooth)))
        previous = gray
    window = scores[1:high_segment_end]
    median = float(np.median(window))
    mad = float(np.median(np.abs(window - median)))
    threshold = median + 8.0 * 1.4826 * mad
    flags = np.zeros(len(frame_paths), dtype=bool)
    flags[1:high_segment_end] = scores[1:high_segment_end] > threshold
    return flags, scores


def compute_frame_metrics(
    frame_paths: list[str],
    video_path: str,
    fps: float,
    boundary_frame: int,
    sample_frames: tuple[int, ...],
    region_dir: str,
) -> dict:
    os.makedirs(region_dir, exist_ok=True)
    capture = cv2.VideoCapture(video_path)
    rows: list[dict] = []
    sample_store: dict[int, dict] = {}
    frame_index = 0
    while True:
        ok, distorted_bgr = capture.read()
        if not ok:
            break
        reference_bgr = cv2.imread(frame_paths[frame_index], cv2.IMREAD_COLOR)
        ref = bt601_luma(reference_bgr)
        dist = bt601_luma(distorted_bgr)

        psnr_value = psnr_global(ref, dist)
        ssim_value = ssim(ref, dist)
        msssim_value = msssim(ref, dist)
        rows.append(
            {
                "frame": frame_index,
                "time_sec": frame_index / fps,
                "segment": "high" if frame_index < boundary_frame else "low",
                "psnr_db": psnr_value,
                "ssim": ssim_value,
                "msssim": msssim_value,
            }
        )

        if frame_index in sample_frames:
            sample_store[frame_index] = {
                "psnr_db": np.float64(psnr_value),
                "ssim": np.float64(ssim_value),
                "msssim": np.float64(msssim_value),
                "psnr_db_distortion": psnr_map(ref, dist),
                "ssim_distortion": (1.0 - ssim_map(ref, dist)).astype(np.float32),
                "msssim_distortion": (
                    1.0 - msssim_map(ref, dist)
                ).astype(np.float32),
            }
        frame_index += 1
    capture.release()
    if frame_index != len(frame_paths):
        raise RuntimeError(
            f"frame count mismatch: video={frame_index} reference={len(frame_paths)}"
        )
    for frame_index, payload in sample_store.items():
        np.savez(
            os.path.join(region_dir, f"frame_{frame_index:06d}.npz"), **payload
        )
    return {"rows": rows, "sample_frames": sorted(sample_store)}


def _effect_size(left: np.ndarray, right: np.ndarray) -> float:
    pooled = np.sqrt(
        (left.var(ddof=1) + right.var(ddof=1)) / 2.0
    )
    if pooled == 0.0:
        return float("inf")
    return float((left.mean() - right.mean()) / pooled)


def analyse_boundary(
    rows: list[dict], boundary_frame: int
) -> list[dict]:
    """Confirm a single sharp change point at the known segment boundary."""
    checks: list[dict] = []
    for metric in METRICS:
        values = np.asarray([row[metric] for row in rows], dtype=np.float64)
        left = values[:boundary_frame]
        right = values[boundary_frame:]
        # Largest mean-shift over a 10-frame window gives the detected edge.
        window = 10
        shifts = np.asarray(
            [
                abs(
                    values[idx:idx + window].mean()
                    - values[idx - window:idx].mean()
                )
                for idx in range(window, len(values) - window)
            ]
        )
        detected = int(np.argmax(shifts) + window)
        effect = _effect_size(left, right)
        absolute_change = float(left.mean() - right.mean())
        checks.append(
            {
                "boundary_frame": boundary_frame,
                "left_segment": "high",
                "right_segment": "low",
                "metric": metric,
                "left_mean": float(left.mean()),
                "right_mean": float(right.mean()),
                "absolute_change": absolute_change,
                "relative_change_pct": float(
                    100.0 * absolute_change / abs(left.mean())
                ),
                "effect_size": effect,
                "detected_frame": detected,
                "detection_error_frames": abs(detected - boundary_frame),
                "clear": abs(detected - boundary_frame) == 0 and abs(effect) > 3.0,
            }
        )
    return checks


def analyse_high_noise(
    rows: list[dict], noise_flags: np.ndarray, boundary_frame: int
) -> list[dict]:
    """Compare clean vs high-noise frames inside the high bitrate segment.

    The low bitrate segment is excluded so the comparison isolates the noise
    effect instead of mixing it with the CRF step change.
    """
    in_high_segment = np.zeros(len(rows), dtype=bool)
    in_high_segment[:boundary_frame] = True
    noise = noise_flags & in_high_segment
    results: list[dict] = []
    for metric in METRICS:
        values = np.asarray([row[metric] for row in rows], dtype=np.float64)
        clean = values[in_high_segment & ~noise_flags]
        noisy = values[noise]
        pooled = np.sqrt(
            (clean.var(ddof=1) + noisy.var(ddof=1)) / 2.0
        )
        effect = float((noisy.mean() - clean.mean()) / pooled)
        delta = float(noisy.mean() - clean.mean())
        results.append(
            {
                "metric": metric,
                "clean_mean": float(clean.mean()),
                "noise_mean": float(noisy.mean()),
                "delta": delta,
                "relative_delta_pct": float(100.0 * delta / abs(clean.mean())),
                "effect_size": effect,
                "clean_frames": int(clean.size),
                "noise_frames": int(noisy.size),
            }
        )
    return results


def _block_means(map_a: np.ndarray, block: int) -> np.ndarray:
    h, w = map_a.shape
    hb, wb = h // block, w // block
    cropped = map_a[: hb * block, : wb * block]
    reshaped = cropped.reshape(hb, block, wb, block)
    return reshaped.mean(axis=(1, 3))


def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    a_flat = a.flatten()
    b_flat = b.flatten()
    if a_flat.std() == 0 or b_flat.std() == 0:
        return 0.0
    return float(np.corrcoef(a_flat, b_flat)[0, 1])


def _shifted_correlation(
    a: np.ndarray, b: np.ndarray, max_shift: int = 2
) -> tuple[float, tuple[int, int]]:
    best = _pearson(a, b)
    best_shift = (0, 0)
    for dy in range(-max_shift, max_shift + 1):
        for dx in range(-max_shift, max_shift + 1):
            if dx == 0 and dy == 0:
                continue
            shifted = np.roll(np.roll(b, dy, axis=0), dx, axis=1)
            score = _pearson(a, shifted)
            if score > best:
                best = score
                best_shift = (dx, dy)
    return best, best_shift


def _centroid(grid: np.ndarray) -> np.ndarray:
    weights = np.clip(grid, 0.0, None)
    total = weights.sum()
    if total <= 0.0:
        return np.asarray([0.0, 0.0])
    ys, xs = np.mgrid[0:grid.shape[0], 0:grid.shape[1]]
    return np.asarray(
        [np.sum(xs * weights) / total, np.sum(ys * weights) / total]
    )


def analyse_alignment(region_dir: str, sample_frames: list[int]) -> dict:
    """Verify that the three distortion maps highlight the same regions."""
    pair_names = (
        ("psnr_db", "ssim"),
        ("psnr_db", "msssim"),
        ("ssim", "msssim"),
    )
    details: list[dict] = []
    for frame in sample_frames:
        path = os.path.join(region_dir, f"frame_{frame:06d}.npz")
        data = np.load(path)
        grids = {
            name: _block_means(data[f"{name}_distortion"], REGION_BLOCK)
            for name in METRICS
        }
        for left_name, right_name in pair_names:
            left = grids[left_name]
            right = grids[right_name]
            pearson = _pearson(left, right)
            best_corr, (shift_x, shift_y) = _shifted_correlation(left, right)
            left_hot = left >= np.percentile(left, HOTSPOT_PERCENTILE)
            right_hot = right >= np.percentile(right, HOTSPOT_PERCENTILE)
            intersection = np.logical_and(left_hot, right_hot).sum()
            union = np.logical_or(left_hot, right_hot).sum()
            iou = float(intersection / union)
            centroid_offset = float(
                np.linalg.norm(_centroid(left) - _centroid(right))
                * REGION_BLOCK
            )
            details.append(
                {
                    "frame": frame,
                    "pair": f"{left_name}-{right_name}",
                    "region_size_px": REGION_BLOCK,
                    "hotspot_percentile": HOTSPOT_PERCENTILE,
                    "pearson": pearson,
                    "hotspot_iou": iou,
                    "centroid_offset_px": centroid_offset,
                    "best_shift_x_blocks": shift_x,
                    "best_shift_y_blocks": shift_y,
                    "best_shift_correlation": best_corr,
                    "aligned": shift_x == 0
                    and shift_y == 0
                    and pearson >= 0.4
                    and iou >= 0.2,
                }
            )
    return {
        "passed": all(item["aligned"] for item in details),
        "samples": len(details),
        "mean_pearson": float(np.mean([d["pearson"] for d in details])),
        "mean_hotspot_iou": float(
            np.mean([d["hotspot_iou"] for d in details])
        ),
        "max_centroid_offset_px": float(
            max(d["centroid_offset_px"] for d in details)
        ),
        "details": details,
        "sample_frames": sample_frames,
    }
