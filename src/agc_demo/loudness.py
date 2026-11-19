"""Loudness measurement following ITU-R BS.1770 (K-weighting).

Mono reduction of the standard: stage 1 is a high-shelf (high-frequency
emphasis), stage 2 a high-pass (rumble protection). Coefficients follow the
reference bilinear-transform design used by pyloudnorm.
"""

from __future__ import annotations

import numpy as np
from scipy.signal import lfilter


def _k_weighting_coeffs(fs: int):
    f0 = 1681.974450955533
    g = 3.999843853973347
    q = 0.7071752369554196
    k = np.tan(np.pi * f0 / fs)
    vh = np.power(10.0, g / 20.0)
    vb = vh ** 2
    a0 = 1.0 + k / q + k * k
    shelf_b = np.array([(vb + vh * k / q + k * k) / a0,
                        2.0 * (k * k - vb) / a0,
                        (vb - vh * k / q + k * k) / a0])
    shelf_a = np.array([1.0, 2.0 * (k * k - 1.0) / a0,
                        (1.0 - k / q + k * k) / a0])

    fp = 38.13547087602444
    qp = 0.5003270373238773
    kp = np.tan(np.pi * fp / fs)
    ap = 1.0 + kp / qp + kp * kp
    hp_b = np.array([1.0 / ap, -2.0 / ap, 1.0 / ap])
    hp_a = np.array([1.0, 2.0 * (kp * kp - 1.0) / ap,
                     (1.0 - kp / qp + kp * kp) / ap])
    return shelf_b, shelf_a, hp_b, hp_a


def k_weight(x: np.ndarray, fs: int) -> np.ndarray:
    b1, a1, b2, a2 = _k_weighting_coeffs(fs)
    return lfilter(b2, a2, lfilter(b1, a1, x))


def momentary_loudness(x: np.ndarray, fs: int, block_ms: float = 400.0,
                       hop_ms: float = 100.0) -> tuple[np.ndarray, np.ndarray]:
    """Return (times_s, momentary loudness LUFS) using overlapping blocks."""
    weighted = k_weight(x, fs)
    block = int(block_ms / 1000.0 * fs)
    hop = int(hop_ms / 1000.0 * fs)
    starts = np.arange(0, max(1, len(x) - block + 1), hop)
    times, lufs = [], []
    for start in starts:
        frame = weighted[start:start + block]
        if frame.size < block:
            break
        ms = np.mean(frame * frame)
        lufs.append(-0.691 + 10.0 * np.log10(ms + 1e-12))
        times.append((start + block / 2.0) / fs)
    return np.asarray(times), np.asarray(lufs)


def integrated_loudness(x: np.ndarray, fs: int) -> float:
    """Mono integrated loudness (absolute-gated BS.1770 measure)."""
    weighted = k_weight(x, fs)
    block = int(0.4 * fs)
    hop = int(0.1 * fs)
    levels = []
    for start in range(0, len(weighted) - block + 1, hop):
        levels.append(np.mean(weighted[start:start + block] ** 2))
    levels = np.asarray(levels)
    if not levels.size:
        return -70.0
    # Absolute gate at -70 LUFS, then relative gate 10 LU below ungated mean.
    abs_gate = 10.0 ** ((-70.0 + 0.691) / 10.0)
    kept = levels[levels > abs_gate]
    if not kept.size:
        return -70.0
    ungated = -0.691 + 10.0 * np.log10(np.mean(kept))
    rel_gate = 10.0 ** ((ungated - 10.0 + 0.691) / 10.0)
    kept = levels[levels > rel_gate]
    if not kept.size:
        return float(ungated)
    return float(-0.691 + 10.0 * np.log10(np.mean(kept)))


def rms_dbfs(x: np.ndarray) -> float:
    return float(20.0 * np.log10(np.sqrt(np.mean(x * x)) + 1e-12))
