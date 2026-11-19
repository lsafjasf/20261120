"""Deterministic synthetic speech + music mixture.

No external audio assets are required: speech is built from harmonic voiced
excitation shaped by a syllable-rate envelope, music from a slowly evolving
chord pad. Stems are returned separately so objective intelligibility metrics
can use the clean speech as a reference.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


FS = 16000
DURATION_S = 12.0

# (start_s, end_s, f0_hz) voiced speech segments; gaps are pauses.
SPEECH_SEGMENTS = (
    (0.5, 2.5, 120.0),
    (3.0, 5.0, 132.0),
    (5.5, 8.0, 110.0),
    (8.5, 11.0, 140.0),
)


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x * x)) + 1e-12)


def _scale_to_dbfs(x: np.ndarray, target_dbfs: float) -> np.ndarray:
    target_rms = 10.0 ** (target_dbfs / 20.0)
    return x * (target_rms / _rms(x))


def _voiced(f0: float, dur_s: float, fs: int, rng: np.random.Generator) -> np.ndarray:
    """Harmonic voiced source with formant-ish spectral tilt and jitter."""
    n = int(dur_s * fs)
    t = np.arange(n) / fs
    phase = 2.0 * np.pi * f0 * t + 0.01 * np.cumsum(rng.standard_normal(n)) / fs
    sig = np.zeros(n)
    for harmonic, gain in ((1, 1.0), (2, 0.55), (3, 0.32), (4, 0.18), (5, 0.10)):
        sig += gain * np.sin(harmonic * phase + 0.3 * harmonic)
    # Syllable-rate amplitude modulation (3.5-5 Hz) mimics word stress.
    syllable = 0.55 + 0.45 * np.sin(2 * np.pi * rng.uniform(3.2, 4.8) * t
                                    + rng.uniform(0, 2 * np.pi))
    # Fade in/out each voiced segment to avoid switching clicks.
    fade = np.minimum(np.minimum(t / 0.05, (dur_s - t) / 0.05), 1.0)
    return sig * syllable * np.clip(fade, 0.0, 1.0)


def build_speech(fs: int = FS, duration_s: float = DURATION_S,
                 rms_dbfs: float = -20.0) -> np.ndarray:
    rng = np.random.default_rng(20261120)
    out = np.zeros(int(duration_s * fs))
    for start, end, f0 in SPEECH_SEGMENTS:
        i0, i1 = int(start * fs), int(end * fs)
        out[i0:i1] += _voiced(f0, end - start, fs, rng)
    # Low-level aspiration noise gives the pauses a realistic floor.
    out += 0.01 * rng.standard_normal(out.size)
    return _scale_to_dbfs(out, rms_dbfs)


def build_music(fs: int = FS, duration_s: float = DURATION_S,
                rms_dbfs: float = -26.0) -> np.ndarray:
    """Chord pad (4 bars) with gentle vibrato plus a soft bass line."""
    rng = np.random.default_rng(77)
    n = int(duration_s * fs)
    t = np.arange(n) / fs
    chords = ((220.0, 277.18, 329.63), (196.0, 246.94, 293.66),
              (174.61, 220.0, 261.63), (196.0, 246.94, 329.63))
    bar_len = duration_s / len(chords)
    pad = np.zeros(n)
    for bar, chord in enumerate(chords):
        i0, i1 = int(bar * bar_len * fs), int((bar + 1) * bar_len * fs)
        tt = t[i0:i1] - bar * bar_len
        vib = 1.0 + 0.004 * np.sin(2 * np.pi * 5.0 * tt)
        for freq in chord:
            pad[i0:i1] += np.sin(2 * np.pi * freq * vib * tt) / len(chord)
    env = 0.5 * (1.0 + np.sin(2 * np.pi * 0.25 * t - np.pi / 2))
    bass = 0.4 * np.sin(2 * np.pi * 55.0 * t) * (0.5 + 0.5 * env)
    music = pad * (0.6 + 0.4 * env) + bass
    music += 0.02 * rng.standard_normal(n)
    return _scale_to_dbfs(music, rms_dbfs)


@dataclass
class Mixture:
    fs: int
    mix: np.ndarray
    speech: np.ndarray
    music: np.ndarray
    speech_intervals: tuple


def build_mixture(fs: int = FS, duration_s: float = DURATION_S,
                  speech_dbfs: float = -20.0, music_dbfs: float = -26.0,
                  step_at_s: float = 6.0, step_db: float = -8.0) -> Mixture:
    """Create the mix and inject a level step to challenge the AGC."""
    speech = build_speech(fs, duration_s, speech_dbfs)
    music = build_music(fs, duration_s, music_dbfs)
    mix = speech + music
    step = np.ones_like(mix)
    step[int(step_at_s * fs):] *= 10.0 ** (step_db / 20.0)
    mix *= step
    return Mixture(fs=fs, mix=mix, speech=speech, music=music,
                   speech_intervals=SPEECH_SEGMENTS)
