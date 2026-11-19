"""Frame-level picture quality metrics (PSNR / SSIM / MS-SSIM).

All metrics are computed on the BT.601 luma plane.  The hot paths use
OpenCV (Gaussian blur, downsampling, image IO and video decoding) so the
per-frame cost stays low even without a GPU.
"""

from __future__ import annotations

import cv2
import numpy as np

__all__ = [
    "bt601_luma",
    "gaussian_window",
    "psnr_global",
    "psnr_map",
    "ssim",
    "ssim_map",
    "msssim",
    "msssim_map",
]

C1 = (0.01 * 255.0) ** 2
C2 = (0.03 * 255.0) ** 2
SSIM_SIGMA = 1.5
SSIM_KSIZE = 11
MSSSIM_WEIGHTS = np.asarray(
    [0.0448, 0.2856, 0.3001, 0.2363, 0.1333], dtype=np.float64
)


def bt601_luma(frame: np.ndarray) -> np.ndarray:
    """Return BT.601 luma as float64.

    Accepts grayscale or BGR frames (the OpenCV convention).  BGR inputs are
    converted with the standard BT.601 weights instead of a plain average so
    the result matches a reference luma plane.
    """
    if frame.ndim == 2:
        return frame.astype(np.float64)
    b, g, r = (frame[..., idx].astype(np.float64) for idx in range(3))
    return 0.299 * r + 0.587 * g + 0.114 * b


def gaussian_window(img: np.ndarray) -> np.ndarray:
    return cv2.GaussianBlur(
        img, (SSIM_KSIZE, SSIM_KSIZE), SSIM_SIGMA, borderType=cv2.BORDER_REFLECT
    )


def psnr_global(ref: np.ndarray, dist: np.ndarray) -> float:
    """Global PSNR in dB; +inf for an identical frame."""
    diff = ref - dist
    mse = float(np.mean(diff * diff))
    if mse <= 0.0:
        return float("inf")
    return 10.0 * np.log10((255.0 ** 2) / mse)


def psnr_map(ref: np.ndarray, dist: np.ndarray) -> np.ndarray:
    """Local PSNR-style distortion map.

    The local MSE is smoothed with the same Gaussian window as SSIM and then
    transformed with ``log1p``.  Higher values mean stronger local distortion,
    which keeps the direction consistent with the SSIM/MS-SSIM distortion maps
    used for region alignment.
    """
    local_mse = gaussian_window((ref - dist) ** 2)
    return np.log1p(local_mse).astype(np.float32)


def _ssim_components(ref: np.ndarray, dist: np.ndarray) -> tuple[
    np.ndarray, np.ndarray, np.ndarray
]:
    """Return (contrast-structure map, luminance map, full SSIM map)."""
    mu_r = gaussian_window(ref)
    mu_d = gaussian_window(dist)
    mu_r2 = mu_r * mu_r
    mu_d2 = mu_d * mu_d
    mu_rd = mu_r * mu_d

    sigma_r2 = gaussian_window(ref * ref) - mu_r2
    sigma_d2 = gaussian_window(dist * dist) - mu_d2
    sigma_rd = gaussian_window(ref * dist) - mu_rd

    cs_map = ((2.0 * sigma_rd + C2) / (sigma_r2 + sigma_d2 + C2)).astype(
        np.float32
    )
    luminance = (2.0 * mu_rd + C1) / (mu_r2 + mu_d2 + C1)
    ssim_map = luminance * cs_map
    return cs_map, luminance, ssim_map.astype(np.float32)


def ssim_map(ref: np.ndarray, dist: np.ndarray) -> np.ndarray:
    """Per-pixel SSIM map (11x11 Gaussian window)."""
    return _ssim_components(ref, dist)[2]


def ssim(ref: np.ndarray, dist: np.ndarray) -> float:
    """Mean SSIM over the whole frame."""
    return float(np.mean(ssim_map(ref, dist)))


def _downscale(img: np.ndarray) -> np.ndarray:
    """OpenCV-accelerated 2x low-pass downsampling for MS-SSIM scales."""
    h, w = img.shape
    return cv2.resize(
        img,
        (max(w // 2, 1), max(h // 2, 1)),
        interpolation=cv2.INTER_AREA,
    )


def msssim_map(ref: np.ndarray, dist: np.ndarray) -> np.ndarray:
    """MS-SSIM local similarity map at the original resolution.

    Five scales are evaluated with the standard Wang et al. weights.  The
    finest-scale contrast/structure map is kept at full resolution and carries
    the spatial detail; the four coarser scales are applied as their frame
    mean factors.  Upsampling coarse maps back to full resolution would inject
    interpolation shifts, whereas this construction keeps every metric on one
    undistorted coordinate grid while preserving the five-scale weighting.
    """
    scales = len(MSSSIM_WEIGHTS)
    cur_r, cur_d = ref, dist
    combined: np.ndarray | None = None
    coarse_factor = 1.0
    for scale in range(scales):
        cs_map, _, full_map = _ssim_components(cur_r, cur_d)
        if scale == scales - 1:
            score_map = full_map
        else:
            score_map = cs_map
        score_map = np.clip(score_map, 0.0, 1.0).astype(np.float64)
        if scale == 0:
            combined = np.power(score_map, MSSSIM_WEIGHTS[0])
        else:
            coarse_factor *= np.power(
                float(np.mean(score_map)), MSSSIM_WEIGHTS[scale]
            )
        if scale < scales - 1:
            cur_r = _downscale(cur_r)
            cur_d = _downscale(cur_d)
    assert combined is not None
    return (combined * coarse_factor).astype(np.float32)


def msssim(ref: np.ndarray, dist: np.ndarray) -> float:
    """Five-scale MS-SSIM scalar value."""
    return float(np.mean(msssim_map(ref, dist)))
