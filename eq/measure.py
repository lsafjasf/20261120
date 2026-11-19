"""Empirical frequency-response measurement of the biquad EQ.

Two complementary methods:

* ``log_sweep`` + ``measured_response_sweep``: broadband logarithmic chirp,
  windowed FFT ratio Y/X (sanity-check overview).
* ``probed_response``: one steady-state sine per probe frequency; after the
  transient settles, output amplitude/phase relative to the input gives a
  clean pointwise H(f) estimate (used for numeric comparisons).
"""

from __future__ import annotations

import numpy as np
from scipy import signal

from .biquad import BiquadCoeffs, process_cascade


def log_sweep(fs: float, duration: float = 4.0,
              f0: float = 10.0) -> tuple[np.ndarray, np.ndarray]:
    """Generate a logarithmic sine sweep covering f0..~fs/2 and its time axis."""
    n = int(round(duration * fs))
    t = np.arange(n) / fs
    f1 = fs / 2.0 * 0.999
    k = (f1 / f0) ** (1.0 / duration)
    phase = 2.0 * np.pi * f0 * duration / np.log(f1 / f0) * (
        np.power(k, t) - 1.0
    )
    return np.sin(phase).astype(np.float64), t


def _bank_lfilter(x: np.ndarray, coeffs_list: list[BiquadCoeffs]) -> np.ndarray:
    y = x
    for c in coeffs_list:
        b = np.array([c.b0, c.b1, c.b2])
        a = np.array([1.0, c.a1, c.a2])
        y = signal.lfilter(b, a, y)
    return y


def measured_response_sweep(samples: np.ndarray, filtered: np.ndarray, fs: float,
                            n_fft: int = 1 << 16) -> tuple[np.ndarray, np.ndarray]:
    """Windowed-FFT ratio Y/X on a late block (transients already decayed)."""
    n_fft = min(n_fft, 1 << (len(samples) - 1).bit_length())
    window = np.hanning(n_fft)
    start = (len(samples) - n_fft) // 2
    sl = slice(start, start + n_fft)
    X = np.fft.rfft(samples[sl] * window)
    Y = np.fft.rfft(filtered[sl] * window)
    freqs = np.fft.rfftfreq(n_fft, d=1.0 / fs)
    eps = 1e-9 * np.max(np.abs(X))
    h = Y / np.where(np.abs(X) < eps, eps, X)
    return freqs, h


def probed_response(coeffs_list: list[BiquadCoeffs], fs: float,
                    freqs: np.ndarray, tone_s: float = 1.0,
                    settle_s: float = 0.25) -> np.ndarray:
    """Pointwise measured H(f) using settled steady-state sine probing."""
    h = np.empty(len(freqs), dtype=np.complex128)
    n = int(round(tone_s * fs))
    n_settle = int(round(settle_s * fs))
    t = np.arange(n) / fs
    for i, f in enumerate(freqs):
        x = np.sin(2.0 * np.pi * f * t)
        y = _bank_lfilter(x, coeffs_list)

        ys, ts = y[n_settle:], t[n_settle:]
        xs = x[n_settle:]
        ref = np.exp(1j * 2.0 * np.pi * f * ts)
        # Least-squares complex Fourier coefficient at the probe frequency.
        hx = 2.0 * np.mean(xs * ref)
        hy = 2.0 * np.mean(ys * ref)
        h[i] = hy / hx
    return h


def measure_bank(coeffs_list: list[BiquadCoeffs], fs: float,
                 duration: float = 4.0
                 ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Convenience: sweep -> filter -> measured H(f)."""
    sweep, _ = log_sweep(fs, duration)
    out = process_cascade(sweep, coeffs_list)
    freqs, h = measured_response_sweep(sweep, out, fs)
    return freqs, h, out


def magnitude_peak_freq_db(freqs: np.ndarray, h: np.ndarray,
                           band: tuple[float, float] | None = None
                           ) -> tuple[float, float]:
    """Return (frequency of |H| extremum, extremum magnitude in dB)."""
    mag_db = 20.0 * np.log10(np.maximum(np.abs(h), 1e-12))
    if band is not None:
        mask = (freqs >= band[0]) & (freqs <= band[1])
        freqs, mag_db = freqs[mask], mag_db[mask]
    idx = int(np.argmax(mag_db))
    # Parabolic interpolation on the dB grid for sub-grid peak location.
    if 0 < idx < len(mag_db) - 1:
        y0, y1, y2 = mag_db[idx - 1], mag_db[idx], mag_db[idx + 1]
        denom = y0 - 2.0 * y1 + y2
        if abs(denom) > 1e-15:
            shift = 0.5 * (y0 - y2) / denom
            log_f = np.log(freqs[idx]) + shift * (
                np.log(freqs[idx + 1]) - np.log(freqs[idx - 1])
            )
            return float(np.exp(log_f)), float(y1)
    return float(freqs[idx]), float(mag_db[idx])
