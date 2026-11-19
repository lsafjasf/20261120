"""Metrics for dither / noise-shaping evaluation."""
from __future__ import annotations

import numpy as np
from scipy import signal


def residual(y: np.ndarray, x: np.ndarray) -> np.ndarray:
    """Quantization residual: quantized output minus 24-bit source."""
    return y - x


def spectrum_db(x: np.ndarray, fs: int, nperseg: int = 8192):
    """One-sided power spectral density in dBFS/Hz (Welch)."""
    f, pxx = signal.welch(x, fs=fs, nperseg=nperseg, window="hann")
    return f, 10.0 * np.log10(np.maximum(pxx, 1e-30))


def noise_floor_dbfs(x: np.ndarray, fs: int, fmin=20.0, fmax=None) -> float:
    """Mean PSD level (dBFS/Hz) across a band."""
    f, psd_db = spectrum_db(x, fs)
    fmax = fmax or fs / 2
    mask = (f >= fmin) & (f <= fmax)
    return float(np.mean(psd_db[mask]))


def band_rms_dbfs(x: np.ndarray, fs: int, f_lo: float, f_hi: float) -> float:
    """RMS of x inside [f_lo, f_hi], in dBFS."""
    sos = signal.butter(8, [f_lo, f_hi], btype="bandpass", fs=fs, output="sos")
    xb = signal.sosfilt(sos, x)
    rms = np.sqrt(np.mean(xb**2))
    return float(20.0 * np.log10(max(rms, 1e-20)))


def thd_db(y: np.ndarray, fs: int, f0: float, n_harm: int = 10) -> float:
    """THD of a sine at f0: sqrt(sum harmonic power) / fundamental power."""
    n = y.shape[0]
    win = signal.windows.blackmanharris(n)
    Y = np.abs(np.fft.rfft(y * win))
    freqs = np.fft.rfftfreq(n, 1.0 / fs)

    def bin_power(fc: float) -> float:
        # integrate power in a +-3-bin neighbourhood around fc
        idx = np.argmin(np.abs(freqs - fc))
        lo, hi = max(0, idx - 3), min(len(Y), idx + 4)
        return float(np.sum(Y[lo:hi] ** 2))

    fund = bin_power(f0)
    harm = 0.0
    for h in range(2, n_harm + 1):
        if h * f0 >= fs / 2:
            break
        harm += bin_power(h * f0)
    return float(10.0 * np.log10(max(harm, 1e-30) / max(fund, 1e-30)))


def write_wav16(path: str, x: np.ndarray, fs: int) -> None:
    """Write float audio in [-1, 1) to 16-bit PCM WAV."""
    from scipy.io import wavfile

    pcm = np.clip(x, -1.0, 1.0 - 2.0**-15)
    wavfile.write(path, fs, (pcm * 32768.0).astype(np.int16))
