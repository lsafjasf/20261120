"""Multi-band dynamic compressor with lookahead and soft/hard knee.

Design:
- 3 bands split by 4th-order Linkwitz-Riley crossovers (200 Hz, 2 kHz).
- Per-band peak detector -> static curve (soft or hard knee) ->
  attack/release smoothing -> band gain.
- Lookahead: the gain computer sees the undelayed signal while the audio
  path is delayed by `lookahead` samples, so gain reduction starts before
  the transient arrives. The audio path is only ever delayed, never
  advanced, so transient onsets can never move earlier.
- Compressor-only: gain is clamped to <= 1.0, so silence (or any
  below-threshold signal) never receives gain boost.
"""

from __future__ import annotations

import numpy as np
from scipy.signal import butter, sosfilt


def linkwitz_riley_split(x: np.ndarray, fs: float, fc: float):
    """Causal 4th-order Linkwitz-Riley crossover: returns (low, high).

    Causal (forward-only) filtering is essential: a zero-phase (filtfilt)
    crossover rings *backwards* in time and would smear transients into
    earlier samples, breaking the lookahead/onset guarantees.
    """
    sos = butter(2, fc / (fs / 2), btype="low", output="sos")
    low = sosfilt(sos, sosfilt(sos, x))
    sos_h = butter(2, fc / (fs / 2), btype="high", output="sos")
    high = sosfilt(sos_h, sosfilt(sos_h, x))
    return low, high


def static_curve_db(level_db: np.ndarray, threshold_db: float, ratio: float,
                    knee_db: float) -> np.ndarray:
    """Gain reduction in dB (<= 0). knee_db == 0 -> hard knee."""
    over = level_db - threshold_db
    gr = np.zeros_like(level_db)
    if knee_db <= 0:
        above = over > 0
        gr[above] = over[above] * (1.0 / ratio - 1.0)
        return gr
    half = knee_db / 2.0
    middle = (over > -half) & (over < half)
    above = over >= half
    gr[middle] = (1.0 / ratio - 1.0) * (over[middle] + half) ** 2 / (2.0 * knee_db)
    gr[above] = over[above] * (1.0 / ratio - 1.0)
    return gr


def smooth_envelope(gr_db: np.ndarray, fs: float, attack_ms: float,
                    release_ms: float) -> np.ndarray:
    """One-pole attack/release smoothing on gain-reduction dB."""
    att = np.exp(-1.0 / (fs * attack_ms / 1000.0))
    rel = np.exp(-1.0 / (fs * release_ms / 1000.0))
    out = np.empty_like(gr_db)
    prev = 0.0
    for i, g in enumerate(gr_db):
        coef = att if g < prev else rel
        prev = coef * prev + (1.0 - coef) * g
        out[i] = prev
    return out


class MultibandCompressor:
    def __init__(self, fs: float, threshold_db: float = -20.0, ratio: float = 4.0,
                 knee_db: float = 6.0, attack_ms: float = 5.0,
                 release_ms: float = 100.0, lookahead_ms: float = 5.0,
                 crossovers=(200.0, 2000.0)):
        self.fs = fs
        self.threshold_db = threshold_db
        self.ratio = ratio
        self.knee_db = knee_db
        self.attack_ms = attack_ms
        self.release_ms = release_ms
        self.lookahead = int(round(lookahead_ms * fs / 1000.0))
        self.crossovers = crossovers

    def split_bands(self, x: np.ndarray):
        low, rest = linkwitz_riley_split(x, self.fs, self.crossovers[0])
        mid, high = linkwitz_riley_split(rest, self.fs, self.crossovers[1])
        return [low, mid, high]

    def process(self, x: np.ndarray):
        """Returns output, delayed input, per-band GR dB and applied gains."""
        bands = self.split_bands(x)
        n = len(x)
        la = self.lookahead
        band_gr_db = []
        band_gain = []
        y = np.zeros(n)
        for band in bands:
            level = np.abs(band)
            level_db = 20.0 * np.log10(np.maximum(level, 1e-10))
            gr_db = static_curve_db(level_db, self.threshold_db, self.ratio,
                                    self.knee_db)
            gr_db = smooth_envelope(gr_db, self.fs, self.attack_ms,
                                    self.release_ms)
            band_gr_db.append(gr_db)
            gain = 10.0 ** (gr_db / 20.0)
            gain = np.minimum(gain, 1.0)  # compressor only: never boost
            band_gain.append(gain)
            delayed = np.concatenate([np.zeros(la), band])[:n]
            y += delayed * gain
        delayed_full = np.concatenate([np.zeros(la), x])[:n]
        return {
            "output": y,
            "delayed_input": delayed_full,
            "band_gr_db": np.array(band_gr_db),
            "band_gain": np.array(band_gain),
            "lookahead_samples": la,
        }
