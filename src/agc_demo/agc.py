"""Automatic gain control with target loudness and slew-rate limiting.

Architecture (broadcast-style, ITU-R BS.1770 level detector):
1. K-weighted RMS measured on 400 ms windows at a 100 ms hop, converted to
   per-frame LUFS. The long window ignores syllable-level envelope swings.
2. Level detector smoothed in dB with separate attack/release time constants.
3. Desired gain = target LUFS - smoothed level, clipped to
   [gain_min_db, gain_max_db].
4. Slew-rate limiter bounds total gain movement by max_rate_db_s.
5. Per-sample gain interpolated from frame gains and applied multiplicatively.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .loudness import k_weight

DB_PER_E = 20.0 * np.log10(np.e)  # ~8.686 dB per amplitude factor of e


def time_constant_to_db_per_s(tau_s: float) -> float:
    """dB/s of an exponential amplitude envelope with time constant tau."""
    return DB_PER_E / max(tau_s, 1e-6)


@dataclass
class AGCTrace:
    frame_time_s: np.ndarray
    measured_dbfs: np.ndarray
    desired_gain_db: np.ndarray
    gain_db: np.ndarray
    sample_gain_db: np.ndarray
    output: np.ndarray


def run_agc(x: np.ndarray, fs: int, target_lufs: float = -23.0,
            frame_ms: float = 400.0, hop_ms: float = 100.0,
            attack_s: float = 0.02, release_s: float = 0.2,
            max_rate_db_s: float = 25.0,
            gain_min_db: float = -20.0, gain_max_db: float = 24.0) -> AGCTrace:
    frame_len = int(frame_ms / 1000.0 * fs)
    hop_len = int(hop_ms / 1000.0 * fs)
    starts = np.arange(0, max(1, len(x) - frame_len + 1), hop_len)
    weighted = k_weight(x, fs)

    measured, raw_level = [], []
    for start in starts:
        frame = weighted[start:start + frame_len]
        # BS.1770 block loudness: -0.691 + 10 log10(mean square).
        level_db = -0.691 + 10.0 * np.log10(np.mean(frame ** 2) + 1e-12)
        measured.append(level_db)
        raw_level.append(level_db)

    frame_dt = hop_len / fs
    alpha_att = 1.0 - np.exp(-frame_dt / max(attack_s, 1e-6))
    alpha_rel = 1.0 - np.exp(-frame_dt / max(release_s, 1e-6))

    # Level detector: fast attack on rising levels, slow release on decay.
    smoothed = np.empty(len(raw_level))
    smoothed[0] = raw_level[0]
    for i in range(1, len(raw_level)):
        alpha = alpha_att if raw_level[i] > smoothed[i - 1] else alpha_rel
        smoothed[i] = smoothed[i - 1] + alpha * (raw_level[i] - smoothed[i - 1])

    desired = np.clip(target_lufs - smoothed, gain_min_db, gain_max_db)
    measured = np.asarray(measured)

    gain = np.zeros_like(desired)
    gain[0] = desired[0]
    for i in range(1, len(desired)):
        delta = desired[i] - gain[i - 1]
        step = np.clip(delta, -max_rate_db_s * frame_dt,
                       max_rate_db_s * frame_dt)
        gain[i] = gain[i - 1] + step

    # Linear interpolation from frame centres to per-sample domain.
    frame_centres = (starts + frame_len / 2.0) / fs
    sample_t = np.arange(len(x)) / fs
    sample_gain_db = np.interp(sample_t, frame_centres, gain,
                               left=gain[0], right=gain[-1])
    linear_gain = 10.0 ** (sample_gain_db / 20.0)
    return AGCTrace(frame_time_s=frame_centres, measured_dbfs=measured,
                    desired_gain_db=desired, gain_db=gain,
                    sample_gain_db=sample_gain_db,
                    output=x * linear_gain)


def slew_violations(trace: AGCTrace, fs: int, hop_ms: float = 100.0) -> np.ndarray:
    """Absolute frame-to-frame gain change in dB/s (for rate-limit checks)."""
    dt = hop_ms / 1000.0
    return np.abs(np.diff(trace.gain_db)) / dt
