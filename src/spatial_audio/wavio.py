"""Minimal 16-bit PCM WAV writer (stdlib only)."""

import math
import wave


def write_wav(path: str, channels: list[list[float]], sample_rate: int) -> None:
    """Write interleaved mono/stereo float samples (range -1..1) to WAV."""
    n_frames = min(len(ch) for ch in channels)
    with wave.open(path, "wb") as wav:
        wav.setnchannels(len(channels))
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        frames = bytearray()
        for i in range(n_frames):
            for ch in channels:
                sample = int(max(-1.0, min(1.0, ch[i])) * 32767)
                frames += int(sample).to_bytes(2, "little", signed=True)
        wav.writeframes(bytes(frames))


def sine_sweep(
    duration_s: float,
    sample_rate: int,
    f0: float = 220.0,
    f1: float = 880.0,
    level: float = 0.5,
) -> list[float]:
    """Short log sine sweep used as the dry test signal."""
    n = int(duration_s * sample_rate)
    out = []
    phase = 0.0
    for i in range(n):
        t = i / sample_rate
        freq = f0 * (f1 / f0) ** (t / duration_s)
        phase += 2.0 * math.pi * freq / sample_rate
        env = min(1.0, t / 0.01) * min(1.0, (duration_s - t) / 0.01)
        out.append(level * env * math.sin(phase))
    return out
