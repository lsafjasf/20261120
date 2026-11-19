"""Minimal audio decoding and continuity measurements for generated fixtures."""

from __future__ import annotations

from math import atan2, cos, pi, sqrt, tau
from pathlib import Path
import struct

from .chunks import AiffFile


def _signed(raw: bytes, width: int, endian: str) -> int:
    value = int.from_bytes(raw, endian)
    sign_bit = 1 << (width * 8 - 1)
    if value & sign_bit:
        value -= 1 << (width * 8)
    return value


def _ulaw_decode(byte: int) -> float:
    value = (~byte) & 0xFF
    sign = value & 0x80
    exponent = (value >> 4) & 0x07
    mantissa = value & 0x0F
    magnitude = ((mantissa << 3) + 0x84) << exponent
    magnitude -= 0x84
    return (-magnitude if sign else magnitude) / 32768.0


def _alaw_decode(byte: int) -> float:
    value = byte ^ 0x55
    sign = value & 0x80
    exponent = (value >> 4) & 0x07
    mantissa = value & 0x0F
    if exponent == 0:
        magnitude = (mantissa << 4) + 8
    else:
        magnitude = ((mantissa << 4) + 0x108) << (exponent - 1)
    return (magnitude if sign else -magnitude) / 32768.0


def decode_channels(aiff: AiffFile, max_frames: int | None = None) -> list[list[float]]:
    """Decode PCM, float and G.711 payloads into per-channel floats."""
    comm, ssnd, layout = aiff._require()
    frames = comm.num_sample_frames if max_frames is None else min(max_frames, comm.num_sample_frames)
    start = ssnd.audio_start_absolute
    channels = [[] for _ in range(comm.channels)]

    if layout.family == "ima4":
        raise NotImplementedError("ima4 sample-value decoding is outside the offset-verification scope")
    if layout.family not in ("pcm_be", "pcm_le", "float_be", "g711"):
        raise NotImplementedError(f"cannot decode {layout.family}")

    for frame in range(frames):
        for channel in range(comm.channels):
            location = aiff.sample_location(frame, channel)
            absolute = location["absolute_file_offset"]
            if layout.family == "g711":
                byte = aiff.data[absolute]
                value = _ulaw_decode(byte) if comm.compression_type.lower() == "ulaw" else _alaw_decode(byte)
            else:
                width = (
                    aiff._pcm_byte_width()
                    if layout.family in ("pcm_be", "pcm_le")
                    else int(layout.bytes_per_sample)
                )
                raw = aiff.data[absolute : absolute + width]
                if layout.family == "float_be":
                    value = struct.unpack(">f" if width == 4 else ">d", raw)[0]
                else:
                    endian = "big" if layout.endian == "big" else "little"
                    scale = float(1 << (width * 8 - 1))
                    value = _signed(raw, width, endian) / scale
            channels[channel].append(value)
    return channels


def _chunk(chunk_id: bytes, payload: bytes) -> bytes:
    return chunk_id + len(payload).to_bytes(4, "big") + payload + (b"\x00" if len(payload) & 1 else b"")


def write_fl32_aifc(path: str | Path, channels: list[list[float]], sample_rate: int) -> None:
    from .extended80 import encode_extended80
    from .fixtures import _pstring

    if not channels or len({len(channel) for channel in channels}) != 1:
        raise ValueError("channels must be non-empty and equally sized")
    frames = len(channels[0])
    interleaved = bytearray()
    for frame in range(frames):
        for channel in channels:
            interleaved += struct.pack(">f", channel[frame])
    comm = struct.pack(">HIH", len(channels), frames, 32) + encode_extended80(sample_rate)
    comm += b"FL32" + _pstring("32-bit float")
    ssnd = struct.pack(">II", 0, 0) + bytes(interleaved)
    chunks = (_chunk(b"FVER", (0xA2805140).to_bytes(4, "big")) + _chunk(b"COMM", comm) + _chunk(b"SSND", ssnd))
    body = b"AIFC" + chunks
    Path(path).write_bytes(b"FORM" + len(body).to_bytes(4, "big") + body)


def resample_linear(channels: list[list[float]], target_frames: int) -> list[list[float]]:
    """Continuously interpolated speed change with an exact output length."""
    source_frames = len(channels[0])
    if source_frames < 2 or target_frames < 1:
        raise ValueError("at least two source frames and one target frame are required")
    step = source_frames / target_frames
    output = []
    for channel in channels:
        result = []
        for index in range(target_frames):
            position = index * step
            left = min(int(position), source_frames - 1)
            right = min(left + 1, source_frames - 1)
            fraction = position - left
            result.append(channel[left] + (channel[right] - channel[left]) * fraction)
        output.append(result)
    return output


def wsola_time_stretch(
    channels: list[list[float]],
    target_frames: int,
    window_frames: int = 2048,
    synthesis_hop: int = 1024,
    tolerance: int = 256,
) -> list[list[float]]:
    """Pitch-preserving WSOLA with correlation-aligned overlap joins."""
    source_frames = len(channels[0])
    if source_frames < window_frames * 2 or target_frames < 1:
        raise ValueError("source is too short for the requested WSOLA window")
    if not 0 < synthesis_hop <= window_frames // 2:
        raise ValueError("synthesis_hop must be in (0, window_frames / 2]")

    analysis_hop = synthesis_hop * source_frames / target_frames
    overlap = window_frames - synthesis_hop
    window = [
        0.5 - 0.5 * cos(2 * pi * index / (window_frames - 1))
        for index in range(window_frames)
    ]
    output = [[0.0] * (target_frames + window_frames) for _ in channels]
    max_source_start = source_frames - window_frames

    for block_index in range((target_frames + synthesis_hop - 1) // synthesis_hop + 1):
        output_start = block_index * synthesis_hop
        if output_start >= target_frames:
            break
        desired = round(block_index * analysis_hop)
        best = 0 if block_index == 0 else desired

        if block_index > 0:
            compare_length = min(overlap, target_frames - output_start)
            existing = [
                values[output_start : output_start + compare_length] for values in output
            ]
            lower = max(0, desired - tolerance)
            upper = min(max_source_start, desired + tolerance)
            if upper < lower:
                lower = upper = min(max_source_start, max(0, desired))
            best_score = -float("inf")
            for candidate in range(lower, upper + 1):
                score = 0.0
                energy = 0.0
                for channel_index, values in enumerate(channels):
                    for sample in range(compare_length):
                        source = values[candidate + sample]
                        target = existing[channel_index][sample]
                        score += source * target
                        energy += source * source
                if energy:
                    score /= sqrt(energy)
                if score > best_score:
                    best_score = score
                    best = candidate

        best = min(max_source_start, max(0, best))
        for channel_index, values in enumerate(channels):
            for sample in range(window_frames):
                destination = output_start + sample
                if destination < target_frames:
                    output[channel_index][destination] += values[best + sample] * window[sample]

    return [values[:target_frames] for values in output]


def rms(values: list[float]) -> float:
    return sqrt(sum(value * value for value in values) / len(values)) if values else 0.0


def estimate_omega(values: list[float], sample_rate: int) -> float | None:
    """Estimate a dominant low-frequency angular velocity from zero crossings."""
    crossings = []
    for index in range(1, len(values)):
        before, after = values[index - 1], values[index]
        if before == 0:
            crossings.append(index)
        elif (before < 0) != (after < 0):
            fraction = abs(before) / (abs(before) + abs(after))
            crossings.append(index - 1 + fraction)
    if len(crossings) < 4:
        return None
    half_periods = (crossings[-1] - crossings[0]) / (len(crossings) - 1)
    if half_periods <= 0:
        return None
    return pi / half_periods


def _wrap_angle(angle: float) -> float:
    return (angle + pi) % tau - pi


def phase_jump_degrees(
    values: list[float], boundary: int, omega: float, radius: int = 8
) -> float | None:
    """Measure phase discontinuity with a derivative-based analytic-signal proxy."""
    if omega <= 0 or boundary < 2 or boundary + 1 >= len(values):
        return None
    angles = []
    for index in range(max(1, boundary - radius), min(len(values), boundary + radius + 1)):
        derivative = (values[index] - values[index - 1]) / omega
        angles.append(atan2(derivative, values[index]))
    jumps = [_wrap_angle(after - before) for before, after in zip(angles, angles[1:])]
    return max(abs(jump) for jump in jumps) * 180 / pi


def continuity_metrics(
    channels: list[list[float]], sample_rate: int, boundary: int, window: int = 4096
) -> dict:
    lower = max(1, boundary - window)
    upper = min(len(channels[0]), boundary + window)
    per_channel = []
    for values in channels:
        baseline = [abs(values[i] - 2 * values[i - 1] + values[i - 2]) for i in range(lower + 2, upper)]
        in_baseline = [abs(values[i] - values[i - 1]) for i in range(lower + 1, upper)]
        seam_step = abs(values[boundary] - values[boundary - 1])
        seam_second = abs(values[boundary] - 2 * values[boundary - 1] + values[boundary - 2])
        baseline_step = rms(in_baseline)
        baseline_second = sorted(baseline)[len(baseline) // 2] if baseline else 0.0
        p99_second = sorted(baseline)[min(len(baseline) - 1, int(len(baseline) * 0.99))] if baseline else 0.0
        omega = estimate_omega(
            values[max(0, boundary - sample_rate // 20) : min(len(values), boundary + sample_rate // 20)],
            sample_rate,
        )
        phase_jump = phase_jump_degrees(values, boundary, omega) if omega else None
        step_ratio = seam_step / baseline_step if baseline_step else 0.0
        second_ratio = seam_second / p99_second if p99_second else 0.0
        click = (
            step_ratio > 4.0
            or second_ratio > 8.0
            or (phase_jump is not None and phase_jump > 10.0)
        )
        per_channel.append(
            {
                "seam_step": seam_step,
                "baseline_step_rms": baseline_step,
                "step_ratio": step_ratio,
                "baseline_second_difference_median": baseline_second,
                "second_difference_ratio": second_ratio,
                "phase": {
                    "available": phase_jump is not None,
                    "estimated_frequency_hz": omega / tau * sample_rate if omega else None,
                    "phase_jump_deg": phase_jump,
                },
                "click_detected": click,
            }
        )
    jumps = [item["phase"]["phase_jump_deg"] for item in per_channel if item["phase"]["phase_jump_deg"] is not None]
    return {
        "per_channel": per_channel,
        "max_phase_jump_deg": max(jumps) if jumps else None,
        "phase_continuous": bool(jumps) and max(jumps) < 5.0,
        "click_detected": any(item["click_detected"] for item in per_channel),
    }


def click_scan(channels: list[list[float]], boundary_step: int = 1024) -> dict:
    frames = len(channels[0])
    threshold = 0.01
    click_frames = []
    second_max = 0.0
    for values in channels:
        second = [abs(values[i] - 2 * values[i - 1] + values[i - 2]) for i in range(2, frames)]
        if second:
            second_max = max(second_max, max(second))
        for offset, value in enumerate(second, start=2):
            if value > threshold and (not click_frames or offset - click_frames[-1] > 4):
                click_frames.append(offset)
    boundaries = list(range(boundary_step, frames, boundary_step))
    max_step_ratio = 0.0
    max_boundary_phase = 0.0
    for boundary in boundaries:
        metrics = continuity_metrics(channels, 48000, boundary, window=64)
        max_step_ratio = max(
            max_step_ratio, max(item["step_ratio"] for item in metrics["per_channel"])
        )
        if metrics["max_phase_jump_deg"] is not None:
            max_boundary_phase = max(max_boundary_phase, metrics["max_phase_jump_deg"])
    return {
        "samples": frames,
        "click_count": len(click_frames),
        "click_frames": click_frames[:20],
        "max_abs_second_difference": second_max,
        "threshold": threshold,
        "block_size": boundary_step,
        "block_boundaries_checked": len(boundaries),
        "max_boundary_step_ratio": max_step_ratio,
        "max_boundary_phase_jump_deg": max_boundary_phase,
    }
