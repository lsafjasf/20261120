"""24-bit to 16-bit quantization with TPDF dither and noise shaping.

Pipeline order (per sample, per channel):
    1. shape: subtract filtered past quantization error from the input
    2. dither: add TPDF dither (1 LSB peak, triangular in [-1, +1] LSB)
    3. quantize: round to 16-bit grid with clipping
    4. update error feedback filter state

So shaping always happens *before* quantization (error-feedback topology).
"""
from __future__ import annotations

import numpy as np

BITS_IN = 24
BITS_OUT = 16
LSB = 2.0 ** -(BITS_OUT - 1)  # 16-bit signed full-scale step, float domain [-1, 1)

# Noise-shaper feedback presets: NTF(z) = 1 - B(z), B has taps b1..bk.
SHAPER_PRESETS = {
    "none": [],
    "first_order": [1.0],            # NTF = 1 - z^-1
    "second_order": [2.0, -1.0],     # NTF = (1 - z^-1)^2
    "third_order": [3.0, -3.0, 1.0], # NTF = (1 - z^-1)^3
}


def tpdf_dither(n: int, rng: np.random.Generator) -> np.ndarray:
    """Triangular PDF dither, peak amplitude 1 LSB (2 LSB peak-to-peak)."""
    return (rng.random(n) - rng.random(n)) * LSB


def shape_dither(d: np.ndarray, taps: list[float]) -> np.ndarray:
    """Pass dither through the shaper NTF 1 - B(z) (UV22-style shaped dither).

    With plain in-loop dither the dither itself reaches the output unshaped
    and dominates the low-frequency floor; filtering it with the same NTF
    pushes dither noise to HF together with the quantization error.
    """
    if not taps:
        return d
    ntf = np.concatenate(([1.0], -np.asarray(taps, dtype=np.float64)))
    out = np.convolve(d, ntf)[: d.shape[0]]
    # renormalize to 1 LSB peak so total dither power stays comparable
    peak = np.max(np.abs(out))
    return out * (LSB / peak) if peak > LSB else out


def quantize_16(x: np.ndarray) -> np.ndarray:
    """Round-to-nearest on the 16-bit grid with clipping to [-1, 1-LSB]."""
    q = np.round(x / LSB) * LSB
    return np.clip(q, -1.0, 1.0 - LSB)


def _shape_and_quantize_channel(
    x: np.ndarray,
    dither: np.ndarray,
    taps: list[float],
) -> np.ndarray:
    """Error-feedback loop: shape first, then quantize."""
    n = x.shape[0]
    y = np.empty(n, dtype=np.float64)
    order = len(taps)
    err_hist = np.zeros(order, dtype=np.float64)
    for i in range(n):
        # 1) noise shaping: subtract filtered past error BEFORE quantization
        shaped = x[i] - float(np.dot(taps, err_hist)) if order else x[i]
        # 2) add TPDF dither
        v = shaped + dither[i]
        # 3) quantize to 16-bit
        q = quantize_16(np.array([v]))[0]
        # 4) update error history (most recent first)
        if order:
            err_hist[1:] = err_hist[:-1]
            err_hist[0] = q - v
        y[i] = q
    return y


def quantize_24_to_16(
    x: np.ndarray,
    dither: bool = True,
    shaper: str = "second_order",
    shape_dither_noise: bool = True,
    seed: int = 1234,
) -> np.ndarray:
    """Quantize float audio (24-bit source, [-1,1)) to the 16-bit grid.

    x: shape (n,) or (n, channels).
    """
    if shaper not in SHAPER_PRESETS:
        raise ValueError(f"unknown shaper {shaper!r}")
    taps = SHAPER_PRESETS[shaper]
    rng = np.random.default_rng(seed)
    mono = x.ndim == 1
    xc = x.reshape(-1, 1) if mono else x
    n, ch = xc.shape
    out = np.empty_like(xc)
    for c in range(ch):
        d = tpdf_dither(n, rng) if dither else np.zeros(n)
        if dither and shape_dither_noise:
            d = shape_dither(d, taps)
        out[:, c] = _shape_and_quantize_channel(xc[:, c], d, taps)
    return out[:, 0] if mono else out


def quantize_with_trace(
    x: np.ndarray,
    taps: list[float],
    dither: np.ndarray,
):
    """Debug helper: returns (y, v, e) so tests can verify that the shaped
    signal v = x - B*e_hist + dither is exactly what gets quantized."""
    n = x.shape[0]
    y = np.empty(n)
    v = np.empty(n)
    e = np.empty(n)
    err_hist = np.zeros(len(taps))
    for i in range(n):
        shaped = x[i] - float(np.dot(taps, err_hist)) if len(taps) else x[i]
        v[i] = shaped + dither[i]
        y[i] = quantize_16(np.array([v[i]]))[0]
        e[i] = y[i] - v[i]
        if len(taps):
            err_hist[1:] = err_hist[:-1]
            err_hist[0] = e[i]
    return y, v, e
