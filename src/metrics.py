"""Per-frame fidelity, detail and motion metrics.

All metrics are full-reference: every candidate frame is compared against the
frame at the same index in its reference clip.  Values live in [0, 1] except
PSNR which is expressed in dB.
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage

C1 = 0.01**2
C2 = 0.03**2


def _gaussian_window(size: int = 11, sigma: float = 1.5) -> np.ndarray:
    coords = np.arange(size, dtype=np.float64) - (size - 1) / 2.0
    kernel_1d = np.exp(-(coords**2) / (2 * sigma**2))
    kernel_2d = np.outer(kernel_1d, kernel_1d)
    return kernel_2d / kernel_2d.sum()


WINDOW = _gaussian_window()


def ssim_map(reference: np.ndarray, candidate: np.ndarray) -> np.ndarray:
    """SSIM quality map for one frame using an 11x11 Gaussian (sigma 1.5)."""
    ref = reference.astype(np.float64)
    cand = candidate.astype(np.float64)
    mu_ref = ndimage.convolve(ref, WINDOW, mode="reflect")
    mu_cand = ndimage.convolve(cand, WINDOW, mode="reflect")
    mu_ref_sq = mu_ref * mu_ref
    mu_cand_sq = mu_cand * mu_cand
    mu_cross = mu_ref * mu_cand
    sigma_ref_sq = (
        ndimage.convolve(ref * ref, WINDOW, mode="reflect") - mu_ref_sq
    )
    sigma_cand_sq = (
        ndimage.convolve(cand * cand, WINDOW, mode="reflect") - mu_cand_sq
    )
    sigma_cross = (
        ndimage.convolve(ref * cand, WINDOW, mode="reflect") - mu_cross
    )
    numerator = (2 * mu_cross + C1) * (2 * sigma_cross + C2)
    denominator = (mu_ref_sq + mu_cand_sq + C1) * (
        sigma_ref_sq + sigma_cand_sq + C2
    )
    return numerator / denominator


def ssim(reference: np.ndarray, candidate: np.ndarray) -> float:
    """Mean SSIM; identical frames short-circuit to 1.0."""
    if np.array_equal(reference, candidate):
        return 1.0
    return float(np.mean(ssim_map(reference, candidate)))


def psnr_db(reference: np.ndarray, candidate: np.ndarray) -> float:
    """Luma PSNR in dB; lossless frames return the 99 dB sentinel."""
    mse = float(np.mean((reference.astype(np.float64) - candidate.astype(np.float64)) ** 2))
    if mse == 0.0:
        return 99.0
    return float(10.0 * np.log10(1.0 / mse))


def _sobel_magnitude(frame: np.ndarray) -> np.ndarray:
    grad_x = ndimage.sobel(frame, axis=1, mode="reflect")
    grad_y = ndimage.sobel(frame, axis=0, mode="reflect")
    return np.hypot(grad_x, grad_y)


def detail_components(
    reference: np.ndarray, candidate: np.ndarray
) -> tuple[float, float]:
    """Return (gradient energy ratio, gradient spatial correlation).

    The energy ratio measures retained edge strength; the correlation measures
    whether the surviving edges sit at the same locations.
    """
    if np.array_equal(reference, candidate):
        return 1.0, 1.0
    grad_ref = _sobel_magnitude(reference.astype(np.float64))
    grad_cand = _sobel_magnitude(candidate.astype(np.float64))
    energy_ref = float(np.mean(grad_ref**2))
    energy_cand = float(np.mean(grad_cand**2))
    energy_ratio = min(1.0, energy_cand / energy_ref) if energy_ref > 0 else 1.0
    centered_ref = grad_ref - grad_ref.mean()
    centered_cand = grad_cand - grad_cand.mean()
    denom = float(np.sqrt(np.sum(centered_ref**2) * np.sum(centered_cand**2)))
    correlation = (
        float(np.sum(centered_ref * centered_cand) / denom) if denom > 0 else 1.0
    )
    return energy_ratio, max(0.0, correlation)


def frame_delta(frame_a: np.ndarray, frame_b: np.ndarray) -> float:
    """Mean absolute luma change between two consecutive frames."""
    return float(np.mean(np.abs(frame_a.astype(np.float64) - frame_b.astype(np.float64))))


def motion_index(deltas: np.ndarray, motion_scale: float) -> np.ndarray:
    """Convert per-frame temporal variation into a bounded motion index.

    The reference stream drives the index so that compression noise in the
    candidate cannot inflate the temporal weighting.  Empirically the index
    tracks the reference mean absolute frame difference with a gain near
    1 / motion_scale; the first frame reuses the first measured delta.
    """
    gain = 1.0 / motion_scale
    return gain * deltas
