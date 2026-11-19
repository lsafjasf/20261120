"""Pure-python audio helpers: WAV synthesis, playback resampling, f0 measure.

16-bit mono PCM throughout. Samples are synthesized with a clear
fundamental plus harmonics and a percussive attack transient so both
pitch and onset are easy to verify by ear and by measurement.
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

SR = 44100


def midi_to_freq(midi: int) -> float:
    return 440.0 * 2.0 ** ((midi - 69) / 12.0)


def synth_tone(freq: float, dur: float = 0.6, sr: int = SR,
               tag: float = 0.0) -> list[float]:
    """Harmonic pluck: sharp attack click + decaying harmonic stack.

    `tag` adds a quiet high marker tone so individual round-robin
    takes stay distinguishable by ear.
    """
    n = int(dur * sr)
    out = []
    harmonics = [(1, 1.0), (2, 0.5), (3, 0.33), (4, 0.25), (5, 0.2)]
    for i in range(n):
        t = i / sr
        env = min(1.0, t / 0.004) * math.exp(-3.0 * t)
        s = sum(a * math.sin(2 * math.pi * freq * h * t) for h, a in harmonics)
        if tag:
            s += 0.15 * math.sin(2 * math.pi * tag * t) * math.exp(-8.0 * t)
        if t < 0.003:  # onset click for a crisp, locatable attack
            s += 0.8 * (1.0 - t / 0.003) * math.sin(2 * math.pi * 3000 * t)
        out.append(0.7 * env * s)
    return out


def write_wav(path: str | Path, samples: list[float], sr: int = SR):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = b"".join(
        struct.pack("<h", int(max(-1.0, min(1.0, s)) * 32767)) for s in samples)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(frames)


def read_wav(path: str | Path) -> tuple[list[float], int]:
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        raw = w.readframes(n)
        sw = w.getsampwidth()
        ch = w.getnchannels()
    if sw != 2:
        raise ValueError(f"only 16-bit PCM supported: {path}")
    ints = struct.unpack(f"<{len(raw) // 2}h", raw)
    if ch > 1:
        ints = ints[::ch]
    return [v / 32768.0 for v in ints], sr


def resample_semitones(samples: list[float], semitones: float) -> list[float]:
    """Linear-interpolation pitch shift by `semitones` (changes duration)."""
    if semitones == 0:
        return list(samples)
    ratio = 2.0 ** (semitones / 12.0)
    n_out = int(len(samples) / ratio)
    out = []
    for i in range(n_out):
        pos = i * ratio
        j = int(pos)
        frac = pos - j
        a = samples[j] if j < len(samples) else 0.0
        b = samples[j + 1] if j + 1 < len(samples) else 0.0
        out.append(a + (b - a) * frac)
    return out


def measure_f0(samples: list[float], sr: int = SR,
               fmin: float = 50.0, fmax: float = 2000.0,
               skip: float = 0.02) -> float | None:
    """Autocorrelation pitch estimate; skips the attack transient.

    Returns Hz, or None when no clear periodicity is found.
    """
    start = int(skip * sr)
    seg = samples[start:start + int(0.4 * sr)]
    if len(seg) < sr // 10:
        return None
    mean = sum(seg) / len(seg)
    seg = [s - mean for s in seg]
    energy = sum(s * s for s in seg)
    if energy < 1e-6:
        return None
    lo, hi = int(sr / fmax), int(sr / fmin)
    best_lag, best_val = 0, -1.0
    for lag in range(lo, min(hi, len(seg) - 1)):
        val = sum(seg[i] * seg[i + lag] for i in range(len(seg) - lag))
        val /= (len(seg) - lag)
        if val > best_val:
            best_val, best_lag = val, lag
    if best_lag == 0 or best_val < 0.3 * (energy / len(seg)):
        return None
    # parabolic refinement around the peak
    def ac(lag):
        return sum(seg[i] * seg[i + lag] for i in range(len(seg) - lag)) / (len(seg) - lag)
    y0, y1, y2 = ac(best_lag - 1), best_val, ac(best_lag + 1)
    denom = y0 - 2 * y1 + y2
    shift = 0.5 * (y0 - y2) / denom if denom else 0.0
    return sr / (best_lag + shift)


def mix(tracks: list[list[float]]) -> list[float]:
    n = max((len(t) for t in tracks), default=0)
    out = [0.0] * n
    for t in tracks:
        for i, s in enumerate(t):
            out[i] += s
    peak = max((abs(s) for s in out), default=1.0)
    if peak > 1.0:
        out = [s / peak for s in out]
    return out
