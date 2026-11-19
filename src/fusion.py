"""Fuse fidelity, detail and motion into one segment score.

The three component scores live in [0, 1].  They are combined with fixed
weights (0.45 / 0.35 / 0.20) and the resulting quality index is mapped onto
0-100 through a gamma curve so small losses on near-perfect content remain
visible while bad content is spread across the lower end of the scale.
"""
from __future__ import annotations

from .config import BANDS, CONFIG


def quality_index(fidelity: float, detail: float, motion: float) -> float:
    weights = CONFIG["weights"]
    return (
        weights["fidelity"] * fidelity
        + weights["detail"] * detail
        + weights["motion"] * motion
    )


def fused_score(fidelity: float, detail: float, motion: float) -> float:
    quality = quality_index(fidelity, detail, motion)
    return 100.0 * quality ** CONFIG["scoreGamma"]


def band_for(score: float) -> str:
    for name, low, high in BANDS:
        if low <= score < high or (name == "excellent" and score >= high):
            return name
    return "bad"


def reference_score(luma_ssim: float) -> float:
    """Reference implementation score: plain luma SSIM scaled to 0-100."""
    return 100.0 * luma_ssim
