"""Frame-level measurement and motion-weighted segment pooling."""
from __future__ import annotations

import numpy as np

from . import metrics
from .config import CONFIG


def _clip01(value: float) -> float:
    return float(max(0.0, min(1.0, value)))


def measure_clip(
    ref_y: np.ndarray,
    ref_u: np.ndarray,
    ref_v: np.ndarray,
    cand_y: np.ndarray,
    cand_u: np.ndarray,
    cand_v: np.ndarray,
) -> dict:
    """Compute per-frame metrics and their motion-weighted clip aggregates."""
    frame_count = cand_y.shape[0]
    rows = []

    ref_deltas = np.zeros(frame_count, dtype=np.float64)
    cand_deltas = np.zeros(frame_count, dtype=np.float64)
    for index in range(1, frame_count):
        ref_deltas[index] = metrics.frame_delta(ref_y[index - 1], ref_y[index])
        cand_deltas[index] = metrics.frame_delta(cand_y[index - 1], cand_y[index])
    ref_deltas[0] = ref_deltas[1]
    cand_deltas[0] = cand_deltas[1]

    motion = metrics.motion_index(ref_deltas, CONFIG["motionScale"])
    per_frame_motion_fidelity = _per_frame_motion_fidelity(
        ref_deltas, cand_deltas
    )

    for index in range(frame_count):
        luma_ssim = metrics.ssim(ref_y[index], cand_y[index])
        chroma_u = metrics.ssim(ref_u[index], cand_u[index])
        chroma_v = metrics.ssim(ref_v[index], cand_v[index])
        chroma_ssim = 0.5 * (chroma_u + chroma_v)
        psnr = metrics.psnr_db(ref_y[index], cand_y[index])
        energy, correlation = metrics.detail_components(
            ref_y[index], cand_y[index]
        )
        detail = (
            CONFIG["detailMix"]["gradientEnergy"] * energy
            + CONFIG["detailMix"]["gradientCorrelation"] * correlation
        )
        fidelity_frame = _frame_fidelity(luma_ssim, chroma_ssim, psnr)
        rows.append(
            {
                "index": index,
                "lumaSsim": luma_ssim,
                "chromaSsim": chroma_ssim,
                "psnrDb": psnr,
                "fidelity": fidelity_frame,
                "detailRetention": _clip01(detail),
                "motionFidelity": per_frame_motion_fidelity[index],
                "motionIndex": float(motion[index]),
                "refDelta": float(ref_deltas[index]),
                "candDelta": float(cand_deltas[index]),
            }
        )

    weights = 1.0 + CONFIG["motionWeightBeta"] * motion
    weighted = _weighted_means(rows, weights, frame_count)
    weighted["motionFidelity"] = float(np.mean(per_frame_motion_fidelity))
    weighted["meanPoolingWeight"] = float(np.mean(weights))
    weighted["meanMotionIndex"] = float(np.mean(motion))
    weighted["detailLoss"] = max(0.0, 1.0 - weighted["detailRetention"])
    weighted["frames"] = rows
    return weighted


def _per_frame_motion_fidelity(
    ref_deltas: np.ndarray, cand_deltas: np.ndarray
) -> np.ndarray:
    """One-sided share of reference temporal variation retained in [0, 1].

    Only *lost* motion is penalised: added grain increases frame deltas but is
    already captured by the luma/chroma fidelity components, so double
    counting it here would push lightly-noised clips far below their
    subjective rating.  Blur and compression reduce temporal variation and
    therefore lower this component as expected.
    """
    fidelity = np.ones_like(ref_deltas)
    denom = ref_deltas
    active = denom > 0
    fidelity[active] = cand_deltas[active] / denom[active]
    return np.clip(fidelity, 0.0, 1.0)


def _frame_fidelity(
    luma_ssim: float, chroma_ssim: float, psnr_db: float
) -> float:
    mix = CONFIG["fidelityMix"]
    if psnr_db >= CONFIG["psnrSentinelDb"]:
        psnr_component = 1.0
    else:
        psnr_component = (psnr_db - CONFIG["psnrFloorDb"]) / (
            CONFIG["psnrCeilDb"] - CONFIG["psnrFloorDb"]
        )
    return _clip01(
        mix["lumaSsim"] * luma_ssim
        + mix["chromaSsim"] * chroma_ssim
        + mix["psnr"] * psnr_component
    )


def _weighted_means(rows: list[dict], weights: np.ndarray, frame_count: int) -> dict:
    keys = ["lumaSsim", "chromaSsim", "psnrDb", "fidelity", "detailRetention"]
    totals = {key: 0.0 for key in keys}
    weight_sum = float(weights.sum())
    for index, row in enumerate(rows):
        for key in keys:
            totals[key] += row[key] * weights[index]
    return {key: totals[key] / weight_sum for key in keys}
