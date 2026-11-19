"""Spectral feature extraction: log-mel filterbank + delta coefficients."""

from __future__ import annotations

import numpy as np

N_FFT = 512
FRAME_LEN = 400       # 25 ms @ 16 kHz
HOP = 160             # 10 ms @ 16 kHz
N_MELS = 20
F_MIN, F_MAX = 80.0, 7600.0


def _hz_to_mel(f):
    return 2595.0 * np.log10(1.0 + f / 700.0)


def _mel_to_hz(m):
    return 700.0 * (10.0 ** (m / 2595.0) - 1.0)


def mel_filterbank(fs: int, n_fft: int = N_FFT, n_mels: int = N_MELS):
    mels = np.linspace(_hz_to_mel(F_MIN), _hz_to_mel(F_MAX), n_mels + 2)
    hz = _mel_to_hz(mels)
    bins = np.floor((n_fft + 1) * hz / fs).astype(int)
    n_bins = n_fft // 2 + 1
    bank = np.zeros((n_mels, n_bins))
    for m in range(n_mels):
        left, center, right = bins[m], bins[m + 1], bins[m + 2]
        for k in range(left, center):
            if center > left:
                bank[m, k] = (k - left) / max(1, center - left)
        for k in range(center, right):
            if right > center:
                bank[m, k] = (right - k) / max(1, right - center)
    return bank


def _delta(feat: np.ndarray) -> np.ndarray:
    """Simple first-order difference with edge padding."""
    pad = np.pad(feat, ((1, 1), (0, 0)), mode="edge")
    return (pad[2:] - pad[:-2]) / 2.0


def _delta_delta(feat: np.ndarray) -> np.ndarray:
    return _delta(_delta(feat))


def extract_features(sig: np.ndarray, fs: int, eps: float = 1e-10) -> np.ndarray:
    """Return frame-level feature matrix (n_frames, 2*n_mels).

    Columns 0:n_mels are log-mel energies, n_mels: are deltas.
    Frames whose energy is below a relative floor are dropped (speech VAD).
    """
    from scipy.fft import rfft

    if len(sig) < FRAME_LEN:
        sig = np.pad(sig, (0, FRAME_LEN - len(sig)))
    window = np.hanning(FRAME_LEN)
    n_frames = 1 + (len(sig) - FRAME_LEN) // HOP
    frames = np.stack([
        sig[i * HOP:i * HOP + FRAME_LEN] * window for i in range(n_frames)
    ])
    spectrum = np.abs(rfft(frames, n=N_FFT))
    power = spectrum ** 2

    # Spectral subtraction: the first few frames are leading silence, so use
    # their average spectrum as the noise estimate (over-subtraction factor
    # 1.4, spectral floor 0.1).
    n_noise = max(2, n_frames // 8)
    noise_estimate = power[:n_noise].mean(axis=0)
    cleaned = np.maximum(power - 1.4 * noise_estimate, 0.1 * power)

    bank = mel_filterbank(fs)
    mel_e = cleaned @ bank.T
    log_mel = np.log(mel_e + eps)

    energy = np.sum(cleaned, axis=1)
    # Energy VAD on the denoised spectrum with a fixed ~13 dB-below-peak gate.
    # Operating after spectral subtraction keeps frame counts stable even at
    # low SNR.
    peak = energy.max() + eps
    keep = energy > peak * 0.05
    if keep.sum() < 3:
        keep = np.ones(n_frames, dtype=bool)
    keep = _fill_speech_gaps(keep)
    log_mel = log_mel[keep]

    delta = _delta(log_mel)
    delta2 = _delta_delta(log_mel)
    feat = np.concatenate([log_mel, delta, delta2], axis=1)
    # Per-utterance normalisation (CMVN).
    feat = (feat - feat.mean(axis=0)) / (feat.std(axis=0) + eps)
    return feat.astype(np.float32)


def _fill_speech_gaps(mask: np.ndarray, max_gap: int = 3) -> np.ndarray:
    """Close short gaps inside the detected speech region."""
    idx = np.where(mask)[0]
    if len(idx) < 2:
        return mask
    filled = mask.copy()
    filled[idx[0]:idx[-1] + 1] = True
    return filled
