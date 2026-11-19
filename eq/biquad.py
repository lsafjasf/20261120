"""RBJ Cookbook parametric EQ biquad filters (peaking / low-shelf / high-shelf).

All filters are normalized-difference-form (transposed direct form II)
biquads: H(z) = (b0 + b1 z^-1 + b2 z^-2) / (1 + a1 z^-1 + a2 z^-2).
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np


@dataclass(frozen=True)
class BiquadCoeffs:
    b0: float
    b1: float
    b2: float
    a1: float
    a2: float
    kind: str
    fc: float
    gain_db: float
    q: float

    def as_tuple(self) -> tuple[float, float, float, float, float]:
        return self.b0, self.b1, self.b2, self.a1, self.a2


def design_biquad(
    kind: str,
    fc: float,
    fs: float,
    gain_db: float,
    q: float,
) -> BiquadCoeffs:
    """Return RBJ Cookbook coefficients for the requested filter type."""
    if not 0.0 < fc < fs / 2.0:
        raise ValueError("fc must satisfy 0 < fc < fs/2")
    if q <= 0:
        raise ValueError("Q must be positive")

    kind = kind.lower()
    if kind not in {"peaking", "low_shelf", "high_shelf"}:
        raise ValueError(f"unsupported filter kind: {kind}")

    a = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * math.pi * fc / fs
    cos_w0 = math.cos(w0)
    sin_w0 = math.sin(w0)

    if kind == "peaking":
        alpha = sin_w0 / (2.0 * q)
        b0 = 1.0 + alpha * a
        b1 = -2.0 * cos_w0
        b2 = 1.0 - alpha * a
        a0 = 1.0 + alpha / a
        a1 = -2.0 * cos_w0
        a2 = 1.0 - alpha / a
    else:
        # Shelving filters use shelf slope S = 1/Q (RBJ definition).
        alpha = sin_w0 / 2.0 * math.sqrt((a + 1.0 / a) * (1.0 / q - 1.0) + 2.0)
        two_sqrt_a_alpha = 2.0 * math.sqrt(a) * alpha
        a_plus = a + 1.0
        a_minus = a - 1.0

        if kind == "low_shelf":
            b0 = a * (a_plus - a_minus * cos_w0 + two_sqrt_a_alpha)
            b1 = 2.0 * a * (a_minus - a_plus * cos_w0)
            b2 = a * (a_plus - a_minus * cos_w0 - two_sqrt_a_alpha)
            a0 = a_plus + a_minus * cos_w0 + two_sqrt_a_alpha
            a1 = -2.0 * (a_minus + a_plus * cos_w0)
            a2 = a_plus + a_minus * cos_w0 - two_sqrt_a_alpha
        else:  # high_shelf
            b0 = a * (a_plus + a_minus * cos_w0 + two_sqrt_a_alpha)
            b1 = -2.0 * a * (a_minus + a_plus * cos_w0)
            b2 = a * (a_plus + a_minus * cos_w0 - two_sqrt_a_alpha)
            a0 = a_plus - a_minus * cos_w0 + two_sqrt_a_alpha
            a1 = 2.0 * (a_minus - a_plus * cos_w0)
            a2 = a_plus - a_minus * cos_w0 - two_sqrt_a_alpha

    return BiquadCoeffs(
        b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0,
        kind, fc, gain_db, q,
    )


def process_biquad(samples: np.ndarray, coeffs: BiquadCoeffs,
                   state: tuple[float, float] | None = None
                   ) -> tuple[np.ndarray, tuple[float, float]]:
    """Apply one biquad (TDF-II) and return output plus final state."""
    b0, b1, b2, a1, a2 = coeffs.as_tuple()
    x = np.asarray(samples, dtype=np.float64)
    y = np.empty_like(x)
    z1, z2 = (0.0, 0.0) if state is None else state
    for n in range(x.shape[0]):
        y_n = b0 * x[n] + z1
        z1 = b1 * x[n] - a1 * y_n + z2
        z2 = b2 * x[n] - a2 * y_n
        y[n] = y_n
    return y, (z1, z2)


def process_cascade(samples: np.ndarray,
                    coeffs_list: list[BiquadCoeffs]) -> np.ndarray:
    """Run several biquads in series, passing filter state between them."""
    y = np.asarray(samples, dtype=np.float64)
    for coeffs in coeffs_list:
        y, _ = process_biquad(y, coeffs)
    return y


def design_response(coeffs_list: list[BiquadCoeffs], freqs: np.ndarray,
                    fs: float) -> np.ndarray:
    """Analytical frequency response H(f) of a cascade of biquads."""
    h = np.ones_like(freqs, dtype=np.complex128)
    z = np.exp(-1j * 2.0 * np.pi * freqs / fs)
    for c in coeffs_list:
        h *= (c.b0 + c.b1 * z + c.b2 * z * z) / (1.0 + c.a1 * z + c.a2 * z * z)
    return h


def crossfade_switch(samples: np.ndarray, old: list[BiquadCoeffs],
                     new: list[BiquadCoeffs], switch_idx: int,
                     fade_len: int) -> np.ndarray:
    """Simulate click-free parameter switching via equal-power-free linear crossfade.

    Both filter banks keep running across the whole signal; their outputs are
    crossfaded around `switch_idx` over `fade_len` samples.
    """
    y_old = process_cascade(samples, old)
    y_new = process_cascade(samples, new)
    out = y_old.copy()
    start = switch_idx - fade_len // 2
    end = start + fade_len
    ramp = np.linspace(0.0, 1.0, end - start)
    out[start:end] = (1.0 - ramp) * y_old[start:end] + ramp * y_new[start:end]
    out[end:] = y_new[end:]
    return out
