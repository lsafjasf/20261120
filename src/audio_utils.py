from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy.io import wavfile
from scipy.signal import stft

SAMPLE_RATE = 48_000
FRAME_SIZE = 480
FRAME_SECONDS = FRAME_SIZE / SAMPLE_RATE


def read_wav(path: str | Path) -> np.ndarray:
    rate, data = wavfile.read(path)
    if rate != SAMPLE_RATE:
        raise ValueError(f"{path}: expected {SAMPLE_RATE} Hz, got {rate}")
    if data.ndim != 1:
        data = data.mean(axis=1)
    if np.issubdtype(data.dtype, np.integer):
        scale = float(max(abs(np.iinfo(data.dtype).min), np.iinfo(data.dtype).max))
        data = data.astype(np.float64) / scale
    else:
        data = data.astype(np.float64)
    return data


def write_wav(path: str | Path, signal: np.ndarray) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.asarray(signal, dtype=np.float64).clip(-1.0, 1.0)
    wavfile.write(path, SAMPLE_RATE, np.round(pcm * 32767.0).astype(np.int16))


def rms(signal: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(signal), dtype=np.float64)))


def db(value: float) -> float:
    return float(20.0 * np.log10(max(value, 1e-15)))


def snr_db(reference: np.ndarray, estimate: np.ndarray) -> float:
    n = min(len(reference), len(estimate))
    signal_power = np.sum(np.square(reference[:n]), dtype=np.float64)
    error_power = np.sum(np.square(reference[:n] - estimate[:n]), dtype=np.float64)
    return float(10.0 * np.log10(max(signal_power, 1e-20) / max(error_power, 1e-20)))


def integrated_loudness(signal: np.ndarray) -> float:
    import pyloudnorm as pyln

    meter = pyln.Meter(SAMPLE_RATE)
    return float(meter.integrated_loudness(np.asarray(signal, dtype=np.float64)))


def normalize_loudness(
    signal: np.ndarray,
    target_lufs: float,
    max_gain_db: float = 24.0,
) -> tuple[np.ndarray, float, float]:
    before = integrated_loudness(signal)
    requested = target_lufs - before
    applied = float(np.clip(requested, -max_gain_db, max_gain_db))
    output = signal * (10.0 ** (applied / 20.0))
    peak = float(np.max(np.abs(output))) if len(output) else 0.0
    if peak > 0.999:
        output *= 0.999 / peak
    after = integrated_loudness(output)
    return output, before, after


def align_delay(signal: np.ndarray, delay: int) -> np.ndarray:
    if delay <= 0:
        return signal
    if delay >= len(signal):
        return np.zeros_like(signal)
    return np.concatenate([signal[delay:], np.zeros(delay, dtype=signal.dtype)])


def high_frequency_metrics(
    reference: np.ndarray,
    estimate: np.ndarray,
    split_hz: float = 8_000.0,
) -> dict[str, float]:
    n = min(len(reference), len(estimate))
    reference = reference[:n]
    estimate = estimate[:n]
    frequency, times, z_ref = stft(
        reference,
        fs=SAMPLE_RATE,
        window="hann",
        nperseg=2048,
        noverlap=1536,
        boundary=None,
        padded=False,
    )
    _, _, z_out = stft(
        estimate,
        fs=SAMPLE_RATE,
        window="hann",
        nperseg=2048,
        noverlap=1536,
        boundary=None,
        padded=False,
    )
    p_ref = np.abs(z_ref) ** 2
    p_out = np.abs(z_out) ** 2
    band = frequency >= split_hz
    hf_ref = p_ref[band]
    hf_out = p_out[band]

    ref_relative = hf_ref.sum() / max(p_ref.sum(), 1e-20)
    out_relative = hf_out.sum() / max(p_out.sum(), 1e-20)
    relative_db = 10.0 * np.log10(max(out_relative, 1e-20) / max(ref_relative, 1e-20))
    band_db = 10.0 * np.log10(max(hf_out.sum(), 1e-20) / max(hf_ref.sum(), 1e-20))

    a = np.log10(hf_ref + 1e-12).ravel()
    b = np.log10(hf_out + 1e-12).ravel()
    correlation = float(np.corrcoef(a, b)[0, 1])
    envelope_ref = np.sqrt(hf_ref.sum(axis=0))
    envelope_out = np.sqrt(hf_out.sum(axis=0))
    envelope_correlation = float(np.corrcoef(envelope_ref, envelope_out)[0, 1])
    return {
        "hf_band_energy_change_db": float(band_db),
        "hf_relative_energy_change_db": float(relative_db),
        "hf_log_spectral_correlation": correlation,
        "hf_envelope_correlation": envelope_correlation,
    }


def read_json(path: str | Path) -> dict:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: str | Path, value: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
