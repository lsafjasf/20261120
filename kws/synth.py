"""Synthetic speech-like data generation.

Two keywords are built from formant-synthesised syllables so that the
experiment is fully self-contained (no external corpus required).
"""

from __future__ import annotations

import numpy as np

FS = 16000

# Base formant triplets (F1, F2, F3) and nominal syllable durations (s).
KEYWORD_DEFS = {
    0: {
        "name": "keyword_A",
        "formants": [(760, 1750, 2900), (520, 1900, 2850), (700, 1650, 2750)],
        "durations": (0.16, 0.14, 0.20),
        "f0": (165.0, 125.0),
    },
    1: {
        "name": "keyword_B",
        "formants": [(280, 700, 2100), (330, 760, 2150),
                     (260, 680, 2050), (310, 740, 2120)],
        "durations": (0.10, 0.10, 0.10, 0.14),
        "f0": (110.0, 150.0),
    },
}


def _resonator(freq: float, bw: float, fs: int):
    """Second-order resonator (pole at freq with bandwidth bw)."""
    from scipy.signal import lfilter

    r = np.exp(-np.pi * bw / fs)
    w = 2.0 * np.pi * freq / fs
    a = np.array([1.0, -2.0 * r * np.cos(w), r * r])
    b = np.array([1.0 - r])
    return b, a, lfilter


def _glottal_source(n: int, f0_hi: float, f0_lo: float, rng: np.random.Generator,
                    fs: int) -> np.ndarray:
    """Impulse train with a linear pitch contour and small jitter."""
    src = np.zeros(n)
    phase = 0.0
    idx = 0
    while idx < n:
        t = idx / n
        f0 = f0_hi * (1.0 - t) + f0_lo * t
        step = f0 / fs
        phase += step
        if phase >= 1.0:
            phase -= 1.0
            src[idx] = 1.0
        idx += 1
    jitter = (rng.standard_normal(n) * 0.01).astype(np.float64)
    return src + jitter


def _syllable(formants, duration: float, f0_hi: float, f0_lo: float,
              rng: np.random.Generator, fs: int,
              voiced: bool = True) -> np.ndarray:
    from scipy.signal import lfilter

    n = int(duration * fs)
    if voiced:
        sig = _glottal_source(n, f0_hi, f0_lo, rng, fs)
    else:
        sig = rng.standard_normal(n)
    for freq in formants:
        bw = 0.06 * freq + 40.0
        b, a, _ = _resonator(freq, bw, fs)
        sig = lfilter(b, a, sig)
    # Amplitude envelope: smooth onset / offset.
    env = np.hanning(max(8, n // 4))
    envelope = np.ones(n)
    k = len(env) // 2
    envelope[:k] = env[:k]
    envelope[-k:] = env[-k:]
    sig = sig * envelope
    # Short noisy consonant at the syllable onset.
    nc = int(0.035 * fs)
    noise = rng.standard_normal(nc)
    b, a, _ = _resonator(formants[0] * 1.6, 300.0, fs)
    noise = lfilter(b, a, noise) * 0.25
    sig[:nc] += noise * np.hanning(2 * nc)[:nc]
    return sig


def _speaker(rng: np.random.Generator) -> dict:
    return {
        "pitch_scale": float(rng.uniform(0.82, 1.18)),
        "formant_scale": float(rng.uniform(0.86, 1.14)),
        "dur_jitter": rng.uniform(0.9, 1.1, size=4),
        "gain": float(rng.uniform(0.7, 1.0)),
    }


def generate_keyword(kind: int, rng: np.random.Generator,
                     fs: int = FS) -> np.ndarray:
    """Generate one utterance of keyword `kind` with random speaker variation."""
    spec = KEYWORD_DEFS[kind]
    spk = _speaker(rng)
    parts = []
    nsyl = len(spec["formants"])
    for i in range(nsyl):
        formants = tuple(f * spk["formant_scale"] for f in spec["formants"][i])
        dur = spec["durations"][i] * spk["dur_jitter"][i]
        f0_hi = spec["f0"][0] * spk["pitch_scale"]
        f0_lo = spec["f0"][1] * spk["pitch_scale"]
        parts.append(_syllable(formants, dur, f0_hi, f0_lo, rng, fs))
        if i < nsyl - 1:
            parts.append(np.zeros(int(rng.uniform(0.02, 0.05) * fs)))
    sig = np.concatenate(parts) * spk["gain"]
    pad = int(0.1 * fs)
    sig = np.pad(sig, (pad, pad + rng.integers(0, int(0.05 * fs))))
    return sig.astype(np.float64)


def generate_oov(rng: np.random.Generator, fs: int = FS) -> np.ndarray:
    """Out-of-vocabulary random utterance (hard negative)."""
    n_syl = int(rng.integers(7, 11))
    parts = []
    pitch = float(rng.uniform(85.0, 190.0))
    for i in range(n_syl):
        # Formants drawn from a mid-F2 band that avoids both keyword
        # signatures (keyword_A: high F2 ~1700-1900; keyword_B: low F2 ~700).
        f1 = rng.uniform(400.0, 650.0)
        f2 = rng.uniform(1050.0, 1400.0)
        f3 = rng.uniform(2300.0, 2600.0)
        formants = (f1, f2, f3)
        dur = float(rng.uniform(0.05, 0.09))
        voiced = rng.random() > 0.3
        parts.append(_syllable(formants, dur, pitch, pitch * 0.85,
                                rng, fs, voiced=voiced))
        if i < n_syl - 1:
            parts.append(np.zeros(int(rng.uniform(0.01, 0.03) * fs)))
    sig = np.concatenate(parts) * float(rng.uniform(0.6, 1.0))
    pad = int(0.1 * fs)
    sig = np.pad(sig, (pad, pad + rng.integers(0, int(0.05 * fs))))
    return sig.astype(np.float64)


def generate_noise_segment(rng: np.random.Generator, fs: int = FS) -> np.ndarray:
    """Noise-only negative: white or speech-shaped coloured noise."""
    from scipy.signal import lfilter

    n = int(rng.uniform(0.6, 1.1) * fs)
    raw = rng.standard_normal(n)
    if rng.random() < 0.5:
        b, a, _ = _resonator(rng.uniform(400.0, 1500.0), 400.0, fs)
        raw = lfilter(b, a, raw)
    scale = float(rng.uniform(0.2, 1.0))
    return raw * scale


def add_noise_at_snr(sig: np.ndarray, noise: np.ndarray, snr_db: float,
                     rng: np.random.Generator) -> np.ndarray:
    """Add noise to sig at the requested SNR (dB)."""
    if len(noise) < len(sig):
        reps = int(np.ceil(len(sig) / len(noise)))
        noise = np.tile(noise, reps)
    start = int(rng.integers(0, len(noise) - len(sig) + 1))
    noise = noise[start:start + len(sig)]
    sig_power = np.mean(sig ** 2) + 1e-12
    noise_power = np.mean(noise ** 2) + 1e-12
    target = sig_power / (10.0 ** (snr_db / 10.0))
    noise = noise * np.sqrt(target / noise_power)
    return sig + noise


def time_stretch(x: np.ndarray, rate: float, fs: int = FS,
                 frame_ms: float = 30.0) -> np.ndarray:
    """Change duration by factor 1/rate (rate>1 => faster) using OLA.

    Pitch is preserved; only the time axis is modified.
    """
    if abs(rate - 1.0) < 1e-3:
        return x.copy()
    n_frame = int(frame_ms * fs / 1000.0)
    hop_in = n_frame // 2
    hop_out = max(1, int(round(hop_in / rate)))
    window = np.hanning(n_frame)
    out_len = int(np.ceil(len(x) / hop_in)) * hop_out + n_frame
    out = np.zeros(out_len)
    wsum = np.zeros(out_len)
    pos_in, pos_out = 0, 0
    while pos_in + n_frame <= len(x):
        frame = x[pos_in:pos_in + n_frame] * window
        out[pos_out:pos_out + n_frame] += frame
        wsum[pos_out:pos_out + n_frame] += window
        pos_in += hop_in
        pos_out += hop_out
    wsum[wsum < 1e-8] = 1e-8
    out = out / wsum
    return out[:max(1, int(round(len(x) / rate)))]
