"""Objective metrics: envelope-based intelligibility, pumping, loudness error."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.signal import butter, hilbert, lfilter

from .agc import AGCTrace
from .loudness import momentary_loudness


# ---------------------------------------------------------------------------
# Intelligibility: Envelope Correlation Index (STOI-inspired, 1/3-octave)
# ---------------------------------------------------------------------------

CENTER_FREQS = np.array([160.0, 200.0, 250.0, 315.0, 400.0, 500.0,
                         630.0, 800.0, 1000.0, 1250.0, 2000.0, 3150.0, 4000.0])


def _third_octave_bank(x: np.ndarray, fs: int) -> list[np.ndarray]:
    bands = []
    nyq = fs / 2.0
    for fc in CENTER_FREQS:
        low, high = fc / 2.0 ** (1.0 / 6.0), fc * 2.0 ** (1.0 / 6.0)
        b, a = butter(2, [low / nyq, min(high / nyq, 0.99)], btype="bandpass")
        bands.append(lfilter(b, a, x))
    return bands


def envelope_correlation_index(reference: np.ndarray, processed: np.ndarray,
                               fs: int) -> float:
    """STOI-style mean envelope correlation, clipped to [0, 1]."""
    win = int(0.064 * fs)
    hop = win // 2
    scores = []
    for ref_band, proc_band in zip(_third_octave_bank(reference, fs),
                                   _third_octave_bank(processed, fs)):
        env_ref = np.abs(hilbert(ref_band))
        env_proc = np.abs(hilbert(proc_band))
        for start in range(0, len(reference) - win + 1, hop):
            r = env_ref[start:start + win]
            p = env_proc[start:start + win]
            if np.sqrt(np.mean(r ** 2)) < 0.05 * np.sqrt(np.mean(env_ref ** 2)):
                continue  # skip silent frames
            rn = r - r.mean()
            pn = p - p.mean()
            denom = np.sqrt(np.mean(rn ** 2) * np.mean(pn ** 2)) + 1e-12
            scores.append(np.clip(np.mean(rn * pn) / denom, 0.0, 1.0))
    return float(np.mean(scores)) if scores else 0.0


def speech_to_background(speech: np.ndarray, other: np.ndarray) -> float:
    """Level difference in dB between the speech stem and the background."""
    s = np.sqrt(np.mean(speech ** 2)) + 1e-12
    b = np.sqrt(np.mean(other ** 2)) + 1e-12
    return float(20.0 * np.log10(s / b))


# ---------------------------------------------------------------------------
# Pumping analysis
# ---------------------------------------------------------------------------


@dataclass
class PumpingReport:
    residual_modulation_db: float
    gain_modulation_db: float
    periodicity: float
    jitter_index_db_per_100ms: float
    reversal_rate_per_s: float
    worst_window_reversals: int
    max_gain_rate_db_s: float
    envelope_riding_r: float
    verdict: str


def detect_pumping(trace: AGCTrace, input_signal: np.ndarray,
                   output: np.ndarray, fs: int,
                   target_lufs: float, window_s: float = 1.0) -> PumpingReport:
    times, out_lufs = momentary_loudness(output, fs)
    _, in_lufs = momentary_loudness(input_signal, fs)
    gain_at_out = np.interp(times, trace.frame_time_s, trace.gain_db,
                            left=trace.gain_db[0], right=trace.gain_db[-1])
    # Residual loudness error; a pumping AGC oscillates instead of converging.
    error = out_lufs - target_lufs
    # Pumping = gain riding the signal envelope. On the 400 ms loudness grid
    # a riding gain is strongly anti-correlated with input loudness.
    envelope_riding_r = float(np.corrcoef(gain_at_out, in_lufs)[0, 1])
    # Detrended residual modulation (fast wiggles around the slow correction).
    kernel = max(3, int(2.0 / np.median(np.diff(times))))
    trend = np.convolve(gain_at_out, np.ones(kernel) / kernel, mode="same")
    residual = gain_at_out - trend
    residual_mod = float(np.sqrt(np.mean(residual ** 2)))
    gain_mod = float(np.sqrt(np.mean(
        (gain_at_out - gain_at_out.mean()) ** 2)))
    # Periodicity: fraction of spectral energy between 1-8 Hz (pumping band).
    spec = np.abs(np.fft.rfft(residual - residual.mean()))
    freqs = np.fft.rfftfreq(residual.size, d=np.median(np.diff(times)))
    band = (freqs >= 1.0) & (freqs <= 8.0)
    periodicity = float(spec[band].sum() / (spec.sum() + 1e-12))
    frame_dt = np.median(np.diff(trace.frame_time_s))
    deltas = np.diff(trace.gain_db)
    signs = np.sign(deltas)
    reversals = int(np.sum((signs[1:] != signs[:-1]) & (signs[1:] != 0)))
    duration = trace.frame_time_s[-1] - trace.frame_time_s[0]
    reversal_rate = reversals / max(duration, 1e-9)
    # Jitter index: mean absolute frame-to-frame gain change at a fixed
    # 100 ms cadence. Smooth slow correction stays small; envelope riding
    # (even when each step is tiny) accumulates here after resampling.
    grid = np.arange(trace.frame_time_s[0], trace.frame_time_s[-1], 0.1)
    gain_grid = np.interp(grid, trace.frame_time_s, trace.gain_db)
    jitter_index = float(np.mean(np.abs(np.diff(gain_grid))))

    win_frames = max(2, int(window_s / frame_dt))
    worst = 0
    for i in range(0, max(1, len(trace.gain_db) - win_frames)):
        seg = trace.gain_db[i:i + win_frames]
        seg_sign = np.sign(np.diff(seg))
        worst = max(worst, int(np.sum(
            (seg_sign[1:] != seg_sign[:-1]) & (seg_sign[1:] != 0))))

    max_rate = float(np.max(np.abs(deltas)) / frame_dt)
    if jitter_index > 1.5 or residual_mod > 3.5 or worst > 12:
        verdict = "PUMPING_DETECTED"
    else:
        verdict = "NO_PUMPING"
    return PumpingReport(residual_modulation_db=residual_mod,
                         gain_modulation_db=gain_mod,
                         periodicity=periodicity,
                         jitter_index_db_per_100ms=jitter_index,
                         reversal_rate_per_s=reversal_rate,
                         worst_window_reversals=worst,
                         max_gain_rate_db_s=max_rate,
                         envelope_riding_r=envelope_riding_r,
                         verdict=verdict), times, error, out_lufs


def loudness_error_over_time(output: np.ndarray, fs: int,
                             target_lufs: float):
    times, lufs = momentary_loudness(output, fs)
    return times, lufs - target_lufs


def modulation_fidelity(reference: np.ndarray, processed: np.ndarray,
                        fs: int) -> float:
    """Preservation of syllable-rate (2-8 Hz) speech envelope modulation."""
    env_ref = np.abs(hilbert(reference))
    env_proc = np.abs(hilbert(processed))
    freqs = np.fft.rfftfreq(env_ref.size, d=1.0 / fs)
    spec_ref = np.abs(np.fft.rfft(env_ref - env_ref.mean()))
    spec_proc = np.abs(np.fft.rfft(env_proc - env_proc.mean()))
    band = (freqs >= 2.0) & (freqs <= 8.0)
    return float(np.sum(np.minimum(spec_ref[band], spec_proc[band])) /
                 (np.sum(spec_ref[band]) + 1e-12))


def noise_boost_in_pauses(processed: np.ndarray, fs: int,
                          speech_intervals) -> float:
    """RMS level of the processed signal during speech pauses (dBFS).

 Higher values mean the AGC audibly lifts background noise/music, which
 degrades perceived clarity between words."""
    mask = np.ones(len(processed), dtype=bool)
    for start, end, _ in speech_intervals:
        mask[int(start * fs):int(end * fs)] = False
    pause = processed[mask]
    return float(20.0 * np.log10(np.sqrt(np.mean(pause ** 2)) + 1e-12))


def rms_loudness_error_during_speech(output: np.ndarray, fs: int,
                                     speech_intervals, target_lufs: float,
                                     ) -> float:
    """RMS of momentary loudness error sampled inside voiced intervals."""
    times, lufs = momentary_loudness(output, fs)
    active = np.zeros_like(times, dtype=bool)
    for start, end, _ in speech_intervals:
        active |= (times >= start) & (times <= end)
    err = lufs[active] - target_lufs
    return float(np.sqrt(np.mean(err ** 2)))
