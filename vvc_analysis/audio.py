"""Wideband/narrowband fixture generation and spectral continuity metrics."""

from __future__ import annotations

import cmath
import math
from pathlib import Path
import statistics
import struct
import wave


SAMPLE_RATE = 48_000
LOW_TONES = ((350.0, 0.13), (700.0, 0.10), (1400.0, 0.08), (2600.0, 0.05))
HIGH_TONES = ((4300.0, 0.055), (5600.0, 0.045), (7200.0, 0.035))


def _raised_cosine(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return 0.5 - 0.5 * math.cos(math.pi * value)


def _wideband_gain(
    time_s: float,
    segment_duration: float,
    transition_duration: float,
    smooth: bool,
) -> float:
    segment = int(time_s / segment_duration)
    target = 1.0 if segment & 1 else 0.0
    if not smooth:
        return target
    nearest = round(time_s / segment_duration)
    boundary = nearest * segment_duration
    if nearest > 0 and abs(time_s - boundary) <= transition_duration / 2:
        before = 1.0 if ((nearest - 1) & 1) else 0.0
        after = 1.0 if (nearest & 1) else 0.0
        position = (time_s - (boundary - transition_duration / 2)) / transition_duration
        return before + (after - before) * _raised_cosine(position)
    return target


def synthesize(
    duration_s: float,
    segment_duration_s: float,
    transition_ms: float,
    smooth: bool,
) -> list[float]:
    samples: list[float] = []
    total = int(duration_s * SAMPLE_RATE)
    for index in range(total):
        time_s = index / SAMPLE_RATE
        gain = _wideband_gain(
            time_s, segment_duration_s, transition_ms / 1000.0, smooth
        )
        value = 0.0
        for frequency, amplitude in LOW_TONES:
            value += amplitude * math.sin(2.0 * math.pi * frequency * time_s)
        for frequency, amplitude in HIGH_TONES:
            value += gain * amplitude * math.sin(
                2.0 * math.pi * frequency * time_s
            )
        edge = min(index, total - 1 - index) / (0.02 * SAMPLE_RATE)
        value *= _raised_cosine(edge)
        samples.append(value)
    return samples


def write_wav(path: Path, samples: list[float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = bytearray()
    peak = max(1.0, max(abs(value) for value in samples))
    scale = 0.94 / peak
    for value in samples:
        integer = round(max(-1.0, min(1.0, value * scale)) * 32767)
        pcm.extend(struct.pack("<h", integer))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(bytes(pcm))


def read_wav(path: Path) -> list[float]:
    with wave.open(str(path), "rb") as source:
        if source.getnchannels() != 1 or source.getsampwidth() != 2:
            raise ValueError("expected 16-bit mono PCM")
        frames = source.readframes(source.getnframes())
    return [value[0] / 32768.0 for value in struct.iter_unpack("<h", frames)]


def _fft(values: list[complex]) -> list[complex]:
    count = len(values)
    if count & (count - 1):
        raise ValueError("FFT length must be a power of two")
    result = list(values)
    target = 0
    for source in range(1, count):
        bit = count >> 1
        while target & bit:
            target ^= bit
            bit >>= 1
        target ^= bit
        if source < target:
            result[source], result[target] = result[target], result[source]
    size = 2
    while size <= count:
        root = cmath.exp(-2j * math.pi / size)
        half = size // 2
        for start in range(0, count, size):
            coefficient = 1.0 + 0.0j
            for index in range(start, start + half):
                even = result[index]
                odd = result[index + half] * coefficient
                result[index] = even + odd
                result[index + half] = even - odd
                coefficient *= root
        size <<= 1
    return result


def _spectrum_frames(
    samples: list[float], fft_size: int = 1024, hop: int = 256
) -> list[dict[str, float]]:
    window = [
        0.5 - 0.5 * math.cos(2.0 * math.pi * index / (fft_size - 1))
        for index in range(fft_size)
    ]
    frames: list[dict[str, float]] = []
    for start in range(0, len(samples) - fft_size, hop):
        windowed = [
            samples[start + index] * window[index] for index in range(fft_size)
        ]
        spectrum = _fft([complex(value, 0.0) for value in windowed])
        low_power = 0.0
        high_power = 0.0
        for bin_index in range(1, fft_size // 2):
            frequency = bin_index * SAMPLE_RATE / fft_size
            power = abs(spectrum[bin_index]) ** 2
            if 300.0 <= frequency < 3400.0:
                low_power += power
            elif 3400.0 <= frequency <= 8000.0:
                high_power += power
        frames.append(
            {
                "time_s": (start + fft_size / 2) / SAMPLE_RATE,
                "low_power": low_power,
                "high_power": high_power,
            }
        )
    peak = max(
        (frame["low_power"] + frame["high_power"] for frame in frames),
        default=1.0,
    )
    for frame in frames:
        total = frame["low_power"] + frame["high_power"]
        frame["high_share"] = frame["high_power"] / total if total else 0.0
        frame["high_amplitude"] = math.sqrt(frame["high_power"] / peak)
        frame["low_db"] = 10.0 * math.log10(max(frame["low_power"] / peak, 1e-12))
        frame["high_db"] = 10.0 * math.log10(max(frame["high_power"] / peak, 1e-12))
    return frames


def _max_step(values: list[float]) -> float:
    return max(
        (abs(right - left) for left, right in zip(values, values[1:])),
        default=0.0,
    )


def analyze_band_switch(
    path: Path,
    segment_duration_s: float,
    transition_ms: float,
    hard_control_path: Path | None = None,
) -> dict:
    samples = read_wav(path)
    frames = _spectrum_frames(samples)
    high_share = [frame["high_share"] for frame in frames]
    high_amplitude = [frame["high_amplitude"] for frame in frames]
    steady = [
        frame
        for frame in frames
        if abs(
            ((frame["time_s"] / segment_duration_s) + 0.5)
            % 1.0
            - 0.5
        )
        > (transition_ms / 1000.0) / segment_duration_s
    ]
    low_db = [frame["low_db"] for frame in steady]
    narrow_share = [
        frame["high_share"]
        for frame in steady
        if int(frame["time_s"] / segment_duration_s) % 2 == 0
    ]
    wide_share = [
        frame["high_share"]
        for frame in steady
        if int(frame["time_s"] / segment_duration_s) % 2 == 1
    ]
    clipped = sum(1 for value in samples if abs(value) >= (32767 / 32768))
    max_sample_delta = max(
        (abs(right - left) for left, right in zip(samples, samples[1:])),
        default=0.0,
    )
    result = {
        "sample_rate": SAMPLE_RATE,
        "duration_s": len(samples) / SAMPLE_RATE,
        "sample_count": len(samples),
        "segment_duration_s": segment_duration_s,
        "transition_ms": transition_ms,
        "transition_count": max(0, int(len(samples) / SAMPLE_RATE / segment_duration_s) - 1),
        "switch_times_s": [
            index * segment_duration_s
            for index in range(1, int(len(samples) / SAMPLE_RATE / segment_duration_s))
        ],
        "fft_size": 1024,
        "hop": 256,
        "max_high_share_step": _max_step(high_share),
        "max_high_amplitude_step": _max_step(high_amplitude),
        "max_sample_delta": max_sample_delta,
        "low_band_db_range_steady": max(low_db) - min(low_db),
        "narrowband_high_share_median": statistics.median(narrow_share),
        "wideband_high_share_median": statistics.median(wide_share),
        "clipped_samples": clipped,
        "samples": samples,
        "frames": frames,
    }
    if hard_control_path is not None:
        control_samples = read_wav(hard_control_path)
        control_frames = _spectrum_frames(control_samples)
        result["hard_control_max_high_share_step"] = _max_step(
            [frame["high_share"] for frame in control_frames]
        )
        result["hard_control_max_high_amplitude_step"] = _max_step(
            [frame["high_amplitude"] for frame in control_frames]
        )
        result["smooth_to_hard_step_ratio"] = (
            result["max_high_share_step"]
            / max(result["hard_control_max_high_share_step"], 1e-12)
        )
    result["continuous_transition_pass"] = (
        result["max_high_share_step"] <= 0.05
        and result["max_high_amplitude_step"] <= 0.15
        and result["clipped_samples"] == 0
        and result["low_band_db_range_steady"] <= 0.2
        and result["max_sample_delta"] <= 0.25
    )
    return result


def write_spectrum_csv(frames: list[dict[str, float]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output:
        output.write(
            "time_s,low_band_db,high_band_db,high_band_share,high_band_amplitude\n"
        )
        for frame in frames:
            output.write(
                f"{frame['time_s']:.6f},{frame['low_db']:.6f},"
                f"{frame['high_db']:.6f},{frame['high_share']:.8f},"
                f"{frame['high_amplitude']:.8f}\n"
            )


def write_spectrum_svg(
    frames: list[dict[str, float]], path: Path, duration_s: float
) -> None:
    width, height, margin = 960, 280, 36
    points = []
    for frame in frames:
        x = margin + (width - 2 * margin) * frame["time_s"] / duration_s
        y = height - margin - (height - 2 * margin) * frame["high_share"]
        points.append(f"{x:.2f},{y:.2f}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">',
                '<rect width="100%" height="100%" fill="#101418"/>',
                f'<polyline fill="none" stroke="#39d98a" stroke-width="2" points="{" ".join(points)}"/>',
                f'<line x1="{margin}" y1="{height-margin}" x2="{width-margin}" y2="{height-margin}" stroke="#8090a0"/>',
                f'<text x="{margin}" y="24" fill="#e0e6ed" font-family="monospace">high-band share vs time</text>',
                "</svg>",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
