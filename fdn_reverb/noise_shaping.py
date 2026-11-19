"""Quantization, error-feedback noise shaping, and THD measurement."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import signal as scipy_signal


NOISE_SHAPER_COEFFICIENTS = (-2.0, 1.0)


def quantize(signal: np.ndarray, bits: int = 12) -> np.ndarray:
    """Round a normalized signal to a signed mid-tread PCM grid."""
    if bits < 2:
        raise ValueError("bits must be at least 2")
    samples = np.asarray(signal, dtype=np.float64)
    lsb = 2.0 / (2**bits)
    return np.round(samples / lsb) * lsb


def quantize_noise_shaped(
    signal: np.ndarray,
    bits: int = 12,
    coefficients: tuple[float, ...] = NOISE_SHAPER_COEFFICIENTS,
) -> np.ndarray:
    """Quantize with FIR error feedback.

    ``coefficients`` implement H(z), giving the noise transfer function
    1 + H(z). The default (-2, +1) produces (1 - z^-1)^2, a second-order
    high-pass characteristic.
    """
    if bits < 2:
        raise ValueError("bits must be at least 2")
    samples = np.asarray(signal, dtype=np.float64).reshape(-1)
    lsb = 2.0 / (2**bits)
    history = np.zeros(len(coefficients), dtype=np.float64)
    output = np.empty_like(samples)
    for index, sample in enumerate(samples):
        shaped_error = float(history @ np.asarray(coefficients))
        quantizer_input = sample + shaped_error
        quantized = float(np.round(quantizer_input / lsb) * lsb)
        output[index] = quantized
        history[1:] = history[:-1]
        history[0] = quantized - quantizer_input
    return output


def _tone_amplitudes(
    signal: np.ndarray,
    sample_rate: int,
    fundamental_hz: float,
    harmonics: int,
) -> tuple[float, np.ndarray]:
    samples = np.asarray(signal, dtype=np.float64).reshape(-1)
    window = np.hanning(samples.size)
    spectrum = np.abs(np.fft.rfft(samples * window))
    bin_width = sample_rate / samples.size

    def peak_near(frequency: float) -> float:
        center = int(round(frequency / bin_width))
        low = max(0, center - 2)
        high = min(spectrum.size, center + 3)
        return float(np.max(spectrum[low:high])) if high > low else 0.0

    fundamental = peak_near(fundamental_hz)
    harmonic_peaks = np.array(
        [peak_near(fundamental_hz * order) for order in range(2, harmonics + 1)]
    )
    return fundamental, harmonic_peaks


def thd_db(
    signal: np.ndarray,
    sample_rate: int,
    fundamental_hz: float,
    harmonics: int = 10,
) -> float:
    """Total harmonic distortion relative to the fundamental, in dB."""
    fundamental, harmonic_peaks = _tone_amplitudes(
        signal,
        sample_rate,
        fundamental_hz,
        harmonics,
    )
    if fundamental <= 0.0:
        raise ValueError("fundamental was not found")
    distortion = float(np.sqrt(np.sum(harmonic_peaks**2)))
    return 20.0 * np.log10(max(distortion / fundamental, 1e-30))


@dataclass(frozen=True)
class NoiseShapingResult:
    samples: np.ndarray
    thd_before_db: float
    thd_after_db: float
    bits: int


def evaluate_noise_shaping(
    tone: np.ndarray,
    sample_rate: int,
    fundamental_hz: float,
    bits: int = 12,
) -> NoiseShapingResult:
    before = quantize(tone, bits=bits)
    after = quantize_noise_shaped(tone, bits=bits)
    return NoiseShapingResult(
        samples=after,
        thd_before_db=thd_db(before, sample_rate, fundamental_hz),
        thd_after_db=thd_db(after, sample_rate, fundamental_hz),
        bits=bits,
    )


def welch_spectrum_db(
    signal: np.ndarray,
    sample_rate: int,
    fundamental_hz: float,
    nperseg: int = 16_384,
) -> tuple[np.ndarray, np.ndarray]:
    """Welch spectrum normalized so the fundamental is 0 dB."""
    samples = np.asarray(signal, dtype=np.float64).reshape(-1)
    nperseg = min(nperseg, samples.size)
    frequencies, power = scipy_signal.welch(
        samples,
        fs=sample_rate,
        window="hann",
        nperseg=nperseg,
        noverlap=nperseg // 2,
        scaling="spectrum",
    )
    fundamental_bin = int(np.argmin(np.abs(frequencies - fundamental_hz)))
    reference = max(float(power[fundamental_bin]), 1e-30)
    return frequencies, 10.0 * np.log10(np.maximum(power / reference, 1e-30))
