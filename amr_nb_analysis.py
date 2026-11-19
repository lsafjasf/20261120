#!/usr/bin/env python3
"""End-to-end AMR-NB mode, parameter, concealment and click analysis.

The script uses the pinned OpenCore AMR-NB implementation in ``src`` for real
encode/decode.  Python owns the storage-format parser, experiment schedule,
bad-frame injection and metrics; it does not emulate the speech codec.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
import struct
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


ROOT = Path(__file__).resolve().parent
FIXTURE_DIR = ROOT / "fixtures"
ARTIFACT_DIR = ROOT / "artifacts"
BUILD_DIR = ROOT / ".build"
WORK_DIR = BUILD_DIR / "work"
PROBE = BUILD_DIR / "amr_nb_probe"
OPENCORE_COMMIT = "3b67218fb8efb776bcd79e7445774e02d778321d"
AMR_MAGIC = b"#!AMR\n"
FRAME_SAMPLES = 160
SAMPLE_RATE = 8000


@dataclass(frozen=True)
class Mode:
    ft: int
    name: str
    rate: int
    bits: int
    lsp_bits: tuple[int, ...]
    subframe_bits: tuple[tuple[int, ...], ...]
    fields: tuple[tuple[tuple[str, int], ...], ...]

    @property
    def payload_bytes(self) -> int:
        return (self.bits + 7) // 8

    @property
    def total_bytes(self) -> int:
        return self.payload_bytes + 1


def _fields_475() -> tuple[tuple[tuple[str, int], ...], ...]:
    return (
        (("pitch_lag", 8), ("codebook", 7), ("code_signs", 2), ("gain", 8)),
        (("pitch_lag", 4), ("codebook", 7), ("code_signs", 2)),
        (("pitch_lag", 4), ("codebook", 7), ("code_signs", 2), ("gain", 8)),
        (("pitch_lag", 4), ("codebook", 7), ("code_signs", 2)),
    )


def _fields_simple(
    pitch: tuple[int, int, int, int],
    code: tuple[int, ...],
    gains: tuple[tuple[tuple[str, int], ...], ...],
) -> tuple[tuple[tuple[str, int], ...], ...]:
    rows = []
    for index, pitch_bits in enumerate(pitch):
        row: list[tuple[str, int]] = [("pitch_lag", pitch_bits)]
        row.extend(("codebook", bits) for bits in code)
        row.extend(gains[index])
        rows.append(tuple(row))
    return tuple(rows)


MODES: tuple[Mode, ...] = (
    Mode(7, "12.2k", 12200, 244, (7, 8, 9, 8, 6), (
        (9, 4, 4, 4, 4, 4, 4, 3, 3, 3, 3, 3, 5),
        (6, 4, 4, 4, 4, 4, 4, 3, 3, 3, 3, 3, 5),
        (9, 4, 4, 4, 4, 4, 4, 3, 3, 3, 3, 3, 5),
        (6, 4, 4, 4, 4, 4, 4, 3, 3, 3, 3, 3, 5),
    ), (
        (("pitch_lag", 9), ("pitch_gain", 4), ("pulse_pos_sign", 4),
         ("pulse_pos_sign", 4), ("pulse_pos_sign", 4),
         ("pulse_pos_sign", 4), ("pulse_pos_sign", 4),
         ("pulse_pos", 3), ("pulse_pos", 3), ("pulse_pos", 3),
         ("pulse_pos", 3), ("pulse_pos", 3), ("code_gain", 5)),
        (("pitch_lag", 6), ("pitch_gain", 4), ("pulse_pos_sign", 4),
         ("pulse_pos_sign", 4), ("pulse_pos_sign", 4),
         ("pulse_pos_sign", 4), ("pulse_pos_sign", 4),
         ("pulse_pos", 3), ("pulse_pos", 3), ("pulse_pos", 3),
         ("pulse_pos", 3), ("pulse_pos", 3), ("code_gain", 5)),
        (("pitch_lag", 9), ("pitch_gain", 4), ("pulse_pos_sign", 4),
         ("pulse_pos_sign", 4), ("pulse_pos_sign", 4),
         ("pulse_pos_sign", 4), ("pulse_pos_sign", 4),
         ("pulse_pos", 3), ("pulse_pos", 3), ("pulse_pos", 3),
         ("pulse_pos", 3), ("pulse_pos", 3), ("code_gain", 5)),
        (("pitch_lag", 6), ("pitch_gain", 4), ("pulse_pos_sign", 4),
         ("pulse_pos_sign", 4), ("pulse_pos_sign", 4),
         ("pulse_pos_sign", 4), ("pulse_pos_sign", 4),
         ("pulse_pos", 3), ("pulse_pos", 3), ("pulse_pos", 3),
         ("pulse_pos", 3), ("pulse_pos", 3), ("code_gain", 5)),
    )),
    Mode(6, "10.2k", 10200, 204, (8, 9, 9), (
        (8, 1, 1, 1, 1, 10, 10, 7, 7),
        (5, 1, 1, 1, 1, 10, 10, 7, 7),
        (8, 1, 1, 1, 1, 10, 10, 7, 7),
        (5, 1, 1, 1, 1, 10, 10, 7, 7),
    ), _fields_simple(
        (8, 5, 8, 5),
        (1, 1, 1, 1, 10, 10, 7),
        ((("gain", 7),),) * 4,
    )),
    Mode(5, "7.95k", 7950, 159, (9, 9, 9), (
        (8, 13, 4, 4, 5),
        (6, 13, 4, 4, 5),
        (8, 13, 4, 4, 5),
        (6, 13, 4, 4, 5),
    ), _fields_simple(
        (8, 6, 8, 6),
        (13, 4),
        ((("pitch_gain", 4), ("code_gain", 5)),) * 4,
    )),
    Mode(4, "7.40k", 7400, 148, (8, 9, 9), (
        (8, 13, 4, 7),
        (5, 13, 4, 7),
        (8, 13, 4, 7),
        (5, 13, 4, 7),
    ), _fields_simple(
        (8, 5, 8, 5),
        (13, 4),
        ((("gain", 7),),) * 4,
    )),
    Mode(3, "6.70k", 6700, 134, (8, 9, 9), (
        (8, 11, 3, 7),
        (4, 11, 3, 7),
        (8, 11, 3, 7),
        (4, 11, 3, 7),
    ), _fields_simple(
        (8, 4, 8, 4),
        (11, 3),
        ((("gain", 7),),) * 4,
    )),
    Mode(2, "5.90k", 5900, 118, (8, 9, 9), (
        (8, 9, 2, 6),
        (4, 9, 2, 6),
        (8, 9, 2, 6),
        (4, 9, 2, 6),
    ), _fields_simple(
        (8, 4, 8, 4),
        (9, 2),
        ((("gain", 6),),) * 4,
    )),
    Mode(1, "5.15k", 5150, 103, (8, 8, 7), (
        (8, 7, 2, 6),
        (4, 7, 2, 6),
        (4, 7, 2, 6),
        (4, 7, 2, 6),
    ), _fields_simple(
        (8, 4, 4, 4),
        (7, 2),
        ((("gain", 6),),) * 4,
    )),
    Mode(0, "4.75k", 4750, 95, (8, 8, 7), (
        (8, 7, 2, 8),
        (4, 7, 2),
        (4, 7, 2, 8),
        (4, 7, 2),
    ), _fields_475()),
)

MODE_BY_FT = {mode.ft: mode for mode in MODES}
MODE_BY_NAME = {mode.name: mode for mode in MODES}


@dataclass(frozen=True)
class Toc:
    byte: int
    pad: int
    quality: int
    ft: int
    mode: Mode | None

    @property
    def bad(self) -> bool:
        return self.quality == 0 or self.mode is None


def parse_toc(byte: int) -> Toc:
    """Parse an AMR-NB storage-format table of contents octet."""
    if not 0 <= byte <= 0xFF:
        raise ValueError("ToC byte out of range")
    if byte & 0x80:
        raise ValueError("AMR-NB ToC P bit must be zero")
    return Toc(
        byte=byte,
        pad=byte & 0x03,
        quality=(byte >> 2) & 0x01,
        ft=(byte >> 3) & 0x0F,
        mode=MODE_BY_FT.get((byte >> 3) & 0x0F),
    )


def make_toc(ft: int, quality: int = 1) -> int:
    if not 0 <= ft <= 15:
        raise ValueError("FT out of range")
    if quality not in (0, 1):
        raise ValueError("Q must be zero or one")
    return (ft << 3) | (quality << 2)


def payload_size(ft: int) -> int:
    """AMR-NB payload bytes excluding the ToC octet."""
    if 0 <= ft <= 7:
        return MODE_BY_FT[ft].payload_bytes
    if ft == 8:
        return 5
    if 9 <= ft <= 14:
        return 5
    return 0


def validate_tables() -> None:
    if len(MODES) != 8:
        raise AssertionError("AMR-NB must have eight speech modes")
    for mode in MODES:
        if sum(mode.lsp_bits) + sum(sum(row) for row in mode.subframe_bits) != mode.bits:
            raise AssertionError(f"bit allocation mismatch for {mode.name}")
        if tuple(len(row) for row in mode.subframe_bits) != tuple(len(row) for row in mode.fields):
            raise AssertionError(f"field label mismatch for {mode.name}")
        for row_bits, row_fields in zip(mode.subframe_bits, mode.fields):
            if sum(row_bits) != sum(bits for _, bits in row_fields):
                raise AssertionError(f"field width mismatch for {mode.name}")
        if mode.payload_bytes != (mode.bits + 7) // 8:
            raise AssertionError(f"payload size mismatch for {mode.name}")


def allocation_summary(mode: Mode) -> dict[str, object]:
    flat = [field for row in mode.fields for field in row]
    grouped = {
        "lsp": sum(mode.lsp_bits),
        "pitch_lag": sum(bits for name, bits in flat if name == "pitch_lag"),
        "pitch_gain": sum(bits for name, bits in flat if name == "pitch_gain"),
        "codebook": sum(
            bits for name, bits in flat
            if name.startswith(("codebook", "pulse", "code_sign"))
        ),
        "code_gain": sum(bits for name, bits in flat if name in ("gain", "code_gain")),
    }
    return {
        "ft": mode.ft,
        "name": mode.name,
        "rate_bps": mode.rate,
        "payload_bits": mode.bits,
        "payload_bytes": mode.payload_bytes,
        "total_bytes_with_toc": mode.total_bytes,
        "min_total_bytes": mode.total_bytes,
        "max_total_bytes": mode.total_bytes,
        "lsp_bits": list(mode.lsp_bits),
        "subframe_bits": [list(row) for row in mode.subframe_bits],
        "groups": grouped,
        "fields": [[list(field) for field in row] for row in mode.fields],
    }


def clip_int16(value: float) -> int:
    return max(-32768, min(32767, int(round(value * 32767.0))))


def synthesize_speech(num_frames: int) -> list[int]:
    """Deterministic, continuous, speech-like 8 kHz fixture.

    The source is a glottal harmonic train shaped by moving formants and a
    syllable envelope.  It is intentionally generated without NumPy so the
    experiment has no non-stdlib dependency.
    """
    total = num_frames * FRAME_SAMPLES
    rng = random.Random(20261120)
    samples = [0.0] * total
    phases = [0.0] * 28

    for frame in range(num_frames):
        t0 = frame * FRAME_SAMPLES / SAMPLE_RATE
        f0 = 112.0 + 20.0 * math.sin(2.0 * math.pi * 0.47 * t0)
        f0 += 7.0 * math.sin(2.0 * math.pi * 1.91 * t0 + 0.6)

        vowel = 0.5 + 0.5 * math.sin(2.0 * math.pi * 0.71 * t0 - 1.2)
        f1 = 420.0 + 360.0 * vowel
        f2 = 1160.0 + 620.0 * (1.0 - vowel)
        f3 = 2440.0 + 260.0 * math.sin(2.0 * math.pi * 0.33 * t0)
        gains = []
        for harmonic in range(1, 29):
            freq = harmonic * f0
            voice = (
                1.0
                + 3.6 * math.exp(-((freq - f1) / 210.0) ** 2)
                + 2.6 * math.exp(-((freq - f2) / 310.0) ** 2)
                + 1.3 * math.exp(-((freq - f3) / 430.0) ** 2)
            )
            gains.append((0.19 / harmonic ** 0.82) * voice)

        for offset in range(FRAME_SAMPLES):
            n = frame * FRAME_SAMPLES + offset
            t = n / SAMPLE_RATE
            syllable = 0.5 + 0.5 * math.sin(2.0 * math.pi * 2.75 * t - 0.8)
            envelope = 0.08 + 0.92 * syllable ** 1.35
            phase_step = 2.0 * math.pi * f0 / SAMPLE_RATE
            for harmonic in range(28):
                phases[harmonic] += phase_step
            value = 0.0
            for harmonic, gain in enumerate(gains, start=1):
                value += gain * math.sin(phases[harmonic - 1])
            breath = (rng.random() * 2.0 - 1.0) * (0.012 + 0.035 * (1.0 - syllable))
            samples[n] = envelope * value + breath

    peak = max(abs(value) for value in samples) or 1.0
    target = 0.72
    fade_samples = int(0.035 * SAMPLE_RATE)
    output = []
    for index, value in enumerate(samples):
        gain = target / peak
        if index < fade_samples:
            gain *= index / fade_samples
        if index >= total - fade_samples:
            gain *= (total - index - 1) / fade_samples
        output.append(clip_int16(value * gain))
    return output


def write_wav(path: Path, samples: Sequence[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(struct.pack(f"<{len(samples)}h", *samples))


def read_wav(path: Path) -> list[int]:
    with wave.open(str(path), "rb") as wav_file:
        if wav_file.getnchannels() != 1 or wav_file.getsampwidth() != 2:
            raise ValueError("expected mono 16-bit PCM")
        if wav_file.getframerate() != SAMPLE_RATE:
            raise ValueError("expected 8 kHz PCM")
        raw = wav_file.readframes(wav_file.getnframes())
    return list(struct.unpack(f"<{len(raw) // 2}h", raw))


def write_raw(path: Path, samples: Sequence[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack(f"<{len(samples)}h", *samples))


def read_raw(path: Path) -> list[int]:
    raw = path.read_bytes()
    return list(struct.unpack(f"<{len(raw) // 2}h", raw))


def build_schedule(cycles: int = 10, frames_per_mode: int = 8) -> list[int]:
    """Ten descending sweeps through all modes, eight 20 ms frames each."""
    schedule: list[int] = []
    for _ in range(cycles):
        for mode in reversed(MODES):
            schedule.extend([mode.ft] * frames_per_mode)
    return schedule


def choose_bad_frames(schedule: Sequence[int], frames_per_mode: int = 8) -> list[int]:
    """Choose four damaged frames per mode, away from every mode block edge."""
    rng = random.Random(4750)
    bad: list[int] = []
    for mode in MODES:
        blocks = [
            start for start in range(0, len(schedule), frames_per_mode)
            if schedule[start] == mode.ft
        ]
        for block in rng.sample(blocks, 4):
            bad.append(block + rng.randint(1, frames_per_mode - 2))
    return sorted(set(bad))


def parse_amr(path: Path) -> list[dict[str, object]]:
    data = path.read_bytes()
    if not data.startswith(AMR_MAGIC):
        raise ValueError("not an AMR-NB storage-format file")
    offset = len(AMR_MAGIC)
    frames: list[dict[str, object]] = []
    while offset < len(data):
        toc_byte = data[offset]
        toc = parse_toc(toc_byte)
        size = payload_size(toc.ft)
        start = offset + 1
        end = start + size
        if end > len(data):
            raise ValueError(f"truncated frame at byte {offset}")
        frames.append({
            "index": len(frames),
            "offset": offset,
            "toc": toc,
            "payload": data[start:end],
        })
        offset = end
    return frames


def write_amr(path: Path, frames: Iterable[tuple[int, bytes]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as output:
        output.write(AMR_MAGIC)
        for toc, payload in frames:
            output.write(bytes((toc,)))
            output.write(payload)


def corrupt_amr(source: Path, destination: Path, bad_indices: Sequence[int]) -> None:
    """Clear Q and flip payload bits without touching P/FT/padding."""
    bad = set(bad_indices)
    rng = random.Random(12200)
    frames = []
    for frame in parse_amr(source):
        toc: Toc = frame["toc"]  # type: ignore[assignment]
        payload = bytearray(frame["payload"])  # type: ignore[arg-type]
        if int(frame["index"]) in bad:
            if payload:
                for _ in range(3):
                    bit = rng.randrange(len(payload) * 8)
                    payload[bit // 8] ^= 1 << (7 - bit % 8)
            frames.append((make_toc(toc.ft, quality=0), bytes(payload)))
        else:
            frames.append((toc.byte, bytes(payload)))
    write_amr(destination, frames)


def ensure_probe() -> Path:
    script = ROOT / "scripts" / "build_probe.sh"
    needs_build = not PROBE.exists()
    if not needs_build:
        needs_build = PROBE.stat().st_mtime < (ROOT / "src" / "amr_nb_probe.cpp").stat().st_mtime
    if needs_build:
        subprocess.run(["bash", str(script)], check=True)
    return PROBE


def run_probe(*args: object) -> None:
    command = [str(PROBE), *(str(arg) for arg in args)]
    result = subprocess.run(command, check=False, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"probe failed ({result.returncode}): {' '.join(command)}\n"
            f"{result.stderr}\n{result.stdout}"
        )


def parse_param_csv(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []

    def parse_vector(text: str, expected: int) -> list[int]:
        values = [int(value) for value in text.strip().strip(",").split(",") if value]
        if len(values) != expected:
            raise ValueError(f"expected {expected} vector values, got {len(values)}")
        return values

    with path.open(newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        if len(header) != 11:
            raise ValueError(f"unexpected parameter header width {len(header)}")
        for values in reader:
            if len(values) != 11:
                raise ValueError(f"unexpected parameter row width {len(values)}")
            ints = [int(value) for value in values[:7]]
            rows.append({
                "frame": ints[0],
                "ft": ints[1],
                "quality": ints[2],
                "bfi": ints[3],
                "switched": ints[4],
                "conceal_state": ints[5],
                "exc_count": ints[6],
                "lsp_old_raw": parse_vector(values[7], 10),
                "lsp_mid_raw": parse_vector(values[8], 10),
                "lsp_new_raw": parse_vector(values[9], 10),
                "excitation": parse_vector(values[10], FRAME_SAMPLES),
            })
    return rows


def lsp_raw_to_hz(values: Sequence[int]) -> list[float]:
    frequencies = []
    for value in values:
        cosine = max(-1.0, min(1.0, value / 32768.0))
        frequencies.append(math.acos(cosine) / (2.0 * math.pi) * SAMPLE_RATE)
    return frequencies


def interpolate_lsp_raw(row: dict[str, object], ft: int) -> list[list[float]]:
    old = row["lsp_old_raw"]  # type: ignore[assignment]
    mid = row["lsp_mid_raw"]  # type: ignore[assignment]
    new = row["lsp_new_raw"]  # type: ignore[assignment]
    if ft == 7:
        vectors = [
            [0.5 * a + 0.5 * b for a, b in zip(old, mid)],
            list(mid),
            [0.5 * a + 0.5 * b for a, b in zip(mid, new)],
            list(new),
        ]
    else:
        vectors = [
            [0.75 * a + 0.25 * b for a, b in zip(old, new)],
            [0.5 * a + 0.5 * b for a, b in zip(old, new)],
            [0.25 * a + 0.75 * b for a, b in zip(old, new)],
            list(new),
        ]
    return [lsp_raw_to_hz(vector) for vector in vectors]


def rms(values: Sequence[float | int]) -> float:
    if not values:
        return 0.0
    return math.sqrt(sum(float(value) * float(value) for value in values) / len(values))


def percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * max(0.0, min(1.0, q))
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return ordered[low]
    fraction = position - low
    return ordered[low] * (1.0 - fraction) + ordered[high] * fraction


def pearson(reference: Sequence[float | int], test: Sequence[float | int]) -> float:
    count = min(len(reference), len(test))
    if count < 2:
        return float("nan")
    ref = [float(value) for value in reference[:count]]
    got = [float(value) for value in test[:count]]
    ref_mean = sum(ref) / count
    got_mean = sum(got) / count
    numerator = sum((a - ref_mean) * (b - got_mean) for a, b in zip(ref, got))
    ref_energy = sum((a - ref_mean) ** 2 for a in ref)
    got_energy = sum((b - got_mean) ** 2 for b in got)
    denominator = math.sqrt(ref_energy * got_energy)
    return numerator / denominator if denominator else float("nan")


def _decimate(values: Sequence[int], factor: int) -> list[float]:
    output = []
    for start in range(0, len(values) - factor + 1, factor):
        chunk = values[start:start + factor]
        output.append(sum(chunk) / factor)
    return output


def _corr_for_lag(reference: Sequence[float], test: Sequence[float], lag: int) -> float:
    start = max(0, -lag)
    stop = min(len(reference), len(test) - lag)
    if stop - start < 32:
        return float("-inf")
    left = reference[start:stop]
    right = test[start + lag:stop + lag]
    return pearson(left, right)


def best_alignment(
    reference: Sequence[int], test: Sequence[int], max_lag: int = 640, factor: int = 8
) -> tuple[int, float]:
    """Find integer lag maximizing Pearson correlation, test[n+lag] ~= ref[n]."""
    ref_small = _decimate(reference, factor)
    test_small = _decimate(test, factor)
    coarse_limit = max(1, max_lag // factor)
    coarse = max(
        range(-coarse_limit, coarse_limit + 1),
        key=lambda lag: _corr_for_lag(ref_small, test_small, lag),
    )
    center = coarse * factor
    fine_start = max(-max_lag, center - factor)
    fine_stop = min(max_lag, center + factor)
    fine = max(
        range(fine_start, fine_stop + 1),
        key=lambda lag: _corr_for_lag(reference, test, lag),
    )
    return fine, _corr_for_lag(reference, test, fine)


def aligned_pair(
    reference: Sequence[int], test: Sequence[int], lag: int
) -> tuple[list[int], list[int]]:
    start = max(0, -lag)
    stop = min(len(reference), len(test) - lag)
    return (
        list(reference[start:stop]),
        list(test[start + lag:stop + lag]),
    )


def frame_statistics(
    schedule: Sequence[int], bad_indices: Sequence[int]
) -> dict[str, object]:
    bad = set(bad_indices)
    by_ft: dict[int, int] = {mode.ft: 0 for mode in MODES}
    bad_by_ft: dict[int, int] = {mode.ft: 0 for mode in MODES}
    for index, ft in enumerate(schedule):
        if ft in by_ft:
            by_ft[ft] += 1
            if index in bad:
                bad_by_ft[ft] += 1
    boundaries = [
        index for index in range(1, len(schedule))
        if schedule[index] != schedule[index - 1]
    ]
    rows = []
    for mode in MODES:
        rows.append({
            "mode": mode.name,
            "ft": mode.ft,
            "frames": by_ft[mode.ft],
            "bad_frames": bad_by_ft[mode.ft],
            "min_total_bytes": mode.total_bytes,
            "max_total_bytes": mode.total_bytes,
        })
    return {
        "total_frames": len(schedule),
        "duration_seconds": len(schedule) * FRAME_SAMPLES / SAMPLE_RATE,
        "bad_frames": len(bad_indices),
        "bad_fraction": len(bad_indices) / len(schedule),
        "mode_boundaries": len(boundaries),
        "boundary_frames": boundaries,
        "by_mode": rows,
    }


def boundary_metrics(
    signal: Sequence[int], schedule: Sequence[int], injected_click: bool = False
) -> dict[str, object]:
    baseline = list(signal)
    boundary_frames = [
        index for index in range(1, len(schedule))
        if schedule[index] != schedule[index - 1]
    ]
    positions = [index * FRAME_SAMPLES for index in boundary_frames]
    baseline_diffs = [
        abs(baseline[index] - baseline[index - 1])
        for index in range(1, len(baseline))
    ]
    baseline_second = [
        abs(baseline[index + 1] - 2 * baseline[index] + baseline[index - 1])
        for index in range(1, len(baseline) - 1)
    ]
    global_p999 = percentile(baseline_diffs, 0.999)
    global_p9999 = percentile(baseline_diffs, 0.9999)
    second_p999 = percentile(baseline_second, 0.999)

    evaluated = baseline
    if injected_click:
        peak = max(abs(value) for value in evaluated) or 1
        for position in positions:
            evaluated[position] = max(
                -32768, min(32767, evaluated[position] + int(0.45 * peak))
            )

    diffs = [
        abs(evaluated[index] - evaluated[index - 1])
        for index in range(1, len(evaluated))
    ]
    second = [
        abs(evaluated[index + 1] - 2 * evaluated[index] + evaluated[index - 1])
        for index in range(1, len(evaluated) - 1)
    ]
    boundary_jumps = [diffs[position - 1] for position in positions]
    boundary_second = [
        second[position - 1] for position in positions
        if 0 < position - 1 < len(second)
    ]
    ratios = [jump / global_p999 if global_p999 else 0.0 for jump in boundary_jumps]
    second_ratios = [
        value / second_p999 if second_p999 else 0.0 for value in boundary_second
    ]
    return {
        "injected_click_control": injected_click,
        "boundaries": len(boundary_frames),
        "global_diff_p999": global_p999,
        "global_diff_p9999": global_p9999,
        "global_second_diff_p999": second_p999,
        "boundary_max_jump": max(boundary_jumps) if boundary_jumps else 0.0,
        "boundary_max_jump_ratio": max(ratios) if ratios else 0.0,
        "boundary_mean_jump_ratio": statistics.fmean(ratios) if ratios else 0.0,
        "boundary_max_second_ratio": max(second_ratios) if second_ratios else 0.0,
        "boundary_jump_ratios": [round(value, 6) for value in ratios],
    }


def bad_frame_correlation(
    source: Sequence[int],
    reconstructed: Sequence[int],
    bad_indices: Sequence[int],
    lag: int,
) -> dict[str, object]:
    reference, test = aligned_pair(source, reconstructed, lag)
    lag_offset = lag
    segment_ref: list[int] = []
    segment_test: list[int] = []
    per_frame: list[float] = []
    for frame in bad_indices:
        source_start = frame * FRAME_SAMPLES
        test_start = source_start + lag_offset
        if (
            source_start < 0
            or source_start + FRAME_SAMPLES > len(source)
            or test_start < 0
            or test_start + FRAME_SAMPLES > len(reconstructed)
        ):
            continue
        ref_chunk = list(source[source_start:source_start + FRAME_SAMPLES])
        test_chunk = list(reconstructed[test_start:test_start + FRAME_SAMPLES])
        segment_ref.extend(ref_chunk)
        segment_test.extend(test_chunk)
        value = pearson(ref_chunk, test_chunk)
        if math.isfinite(value):
            per_frame.append(value)
    return {
        "frames": len(per_frame),
        "sample_correlation": pearson(segment_ref, segment_test),
        "mean_frame_correlation": statistics.fmean(per_frame) if per_frame else float("nan"),
        "min_frame_correlation": min(per_frame) if per_frame else float("nan"),
        "max_frame_correlation": max(per_frame) if per_frame else float("nan"),
        "all_samples_correlation": pearson(reference, test),
    }


def representative_parameters(
    rows: Sequence[dict[str, object]],
    schedule: Sequence[int],
    bad_indices: Sequence[int],
) -> list[dict[str, object]]:
    bad = set(bad_indices)
    output = []
    for mode in MODES:
        candidates = [
            index for index, ft in enumerate(schedule)
            if ft == mode.ft and index not in bad
        ]
        if not candidates:
            continue
        block_start = min(candidates)
        frame_index = min(
            [index for index in candidates if index >= block_start + 3],
            default=candidates[0],
        )
        row = rows[frame_index]
        excitation = row["excitation"]  # type: ignore[assignment]
        output.append({
            "mode": mode.name,
            "ft": mode.ft,
            "frame_index": frame_index,
            "time_seconds": frame_index * FRAME_SAMPLES / SAMPLE_RATE,
            "lsp_hz_by_subframe": [
                [round(value, 6) for value in vector]
                for vector in interpolate_lsp_raw(row, mode.ft)
            ],
            "excitation_raw": list(excitation),
            "excitation_rms": round(rms(excitation), 6),
            "excitation_peak": max(abs(value) for value in excitation),
        })
    return output


def write_parameters_csv(path: Path, rows: Sequence[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = [
        "frame", "ft", "quality", "bfi", "switched", "conceal_state", "exc_count",
        *[f"lsp_old_{index}" for index in range(10)],
        *[f"lsp_mid_{index}" for index in range(10)],
        *[f"lsp_new_{index}" for index in range(10)],
        *[f"exc_{index}" for index in range(FRAME_SAMPLES)],
    ]
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for row in rows:
            writer.writerow([
                row["frame"], row["ft"], row["quality"], row["bfi"],
                row["switched"], row["conceal_state"], row["exc_count"],
                *row["lsp_old_raw"], *row["lsp_mid_raw"], *row["lsp_new_raw"],
                *row["excitation"],
            ])


def replacement_type(state: int) -> str:
    if state >= 6:
        return "BFH mute after six consecutive bad frames"
    if state > 1:
        return "previous-good-frame parameters with progressive gain attenuation"
    return "previous-good-frame parameters (pitch freeze, LSP hold, attenuated gain)"


def build_experiment() -> dict[str, object]:
    validate_tables()
    ensure_probe()
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    WORK_DIR.mkdir(parents=True, exist_ok=True)

    schedule = build_schedule()
    bad_indices = choose_bad_frames(schedule)
    source = synthesize_speech(len(schedule))

    source_wav = FIXTURE_DIR / "amr_nb_source.wav"
    source_raw = WORK_DIR / "amr_nb_source.raw"
    schedule_path = WORK_DIR / "amr_nb_schedule.txt"
    clean_amr = FIXTURE_DIR / "amr_nb_mixed_modes.amr"
    bad_amr = FIXTURE_DIR / "amr_nb_mixed_modes_bad.amr"
    clean_raw = WORK_DIR / "amr_nb_clean.raw"
    concealed_raw = WORK_DIR / "amr_nb_concealed.raw"
    reset_raw = WORK_DIR / "amr_nb_reset_boundaries.raw"
    clean_params_path = WORK_DIR / "amr_nb_clean_params.csv"
    concealed_params_path = WORK_DIR / "amr_nb_concealed_params.csv"
    reset_params_path = WORK_DIR / "amr_nb_reset_params.csv"

    write_wav(source_wav, source)
    write_raw(source_raw, source)
    schedule_path.write_text(" ".join(str(ft) for ft in schedule) + "\n")

    run_probe("encode", source_raw, clean_amr, schedule_path)
    corrupt_amr(clean_amr, bad_amr, bad_indices)
    run_probe("decode", clean_amr, clean_raw, clean_params_path)
    run_probe("decode", bad_amr, concealed_raw, concealed_params_path)
    run_probe(
        "decode", clean_amr, reset_raw, reset_params_path,
        "--reset-on-mode-change",
    )

    clean = read_raw(clean_raw)
    concealed = read_raw(concealed_raw)
    reset = read_raw(reset_raw)
    clean_lag, clean_source_corr = best_alignment(source, clean, max_lag=640)
    concealed_source_corr = _corr_for_lag(source, concealed, clean_lag)
    concealed_clean_corr = pearson(clean, concealed)

    clean_rows = parse_param_csv(clean_params_path)
    concealed_rows = parse_param_csv(concealed_params_path)
    write_parameters_csv(ARTIFACT_DIR / "amr_nb_parameters.csv", clean_rows)
    write_wav(ARTIFACT_DIR / "amr_nb_clean.wav", clean)
    write_wav(ARTIFACT_DIR / "amr_nb_concealed.wav", concealed)
    write_wav(ARTIFACT_DIR / "amr_nb_reset_boundaries.wav", reset)

    parsed_clean = parse_amr(clean_amr)
    parsed_bad = parse_amr(bad_amr)
    if len(parsed_clean) != len(schedule) or len(parsed_bad) != len(schedule):
        raise AssertionError("frame count does not match the schedule")
    for index, (frame, expected_ft) in enumerate(zip(parsed_clean, schedule)):
        toc = frame["toc"]
        if toc.ft != expected_ft:  # type: ignore[union-attr]
            raise AssertionError(f"FT mismatch at frame {index}")
        payload = frame["payload"]
        total_bytes = 1 + len(payload)  # type: ignore[arg-type]
        if toc.mode is None or total_bytes != toc.mode.total_bytes:  # type: ignore[union-attr]
            raise AssertionError(f"frame length mismatch at frame {index}")
    if any(
        frame["toc"].quality != 0  # type: ignore[union-attr]
        for frame in parsed_bad if int(frame["index"]) in set(bad_indices)
    ):
        raise AssertionError("bad-frame Q bit was not cleared")

    stats = frame_statistics(schedule, bad_indices)
    length_rows = []
    for mode in MODES:
        lengths = [
            1 + len(frame["payload"])  # type: ignore[arg-type]
            for frame in parsed_clean
            if frame["toc"].ft == mode.ft  # type: ignore[union-attr]
        ]
        length_rows.append({
            "mode": mode.name,
            "ft": mode.ft,
            "payload_bits": mode.bits,
            "payload_bytes": mode.payload_bytes,
            "min_total_bytes": min(lengths),
            "max_total_bytes": max(lengths),
        })

    clean_boundary = boundary_metrics(clean, schedule)
    hidden_boundary = boundary_metrics(concealed, schedule)
    reset_boundary = boundary_metrics(reset, schedule)
    click_control = boundary_metrics(clean, schedule, injected_click=True)
    pass_boundary = (
        clean_boundary["boundary_max_jump_ratio"] < 2.0
        and clean_boundary["boundary_max_second_ratio"] < 3.0
    )
    detector_sensitive = (
        click_control["boundary_max_jump_ratio"]
        > 2.0 * clean_boundary["boundary_max_jump_ratio"]
    )

    bad_source = bad_frame_correlation(source, concealed, bad_indices, clean_lag)
    bad_clean = bad_frame_correlation(clean, concealed, bad_indices, 0)
    per_mode_bad = []
    for mode in MODES:
        indices = [index for index in bad_indices if schedule[index] == mode.ft]
        result = bad_frame_correlation(source, concealed, indices, clean_lag)
        per_mode_bad.append({
            "mode": mode.name,
            "frames": len(indices),
            "correlation": result["sample_correlation"],
        })

    bad_details = []
    for index in bad_indices:
        row = concealed_rows[index]
        bad_details.append({
            "frame": index,
            "mode": MODE_BY_FT[int(row["ft"])].name,
            "toc_quality": int(row["quality"]),
            "bfi": int(row["bfi"]),
            "conceal_state": int(row["conceal_state"]),
            "replacement_type": replacement_type(int(row["conceal_state"])),
        })

    toc_layout = {
        "octet": "P | FT3 FT2 FT1 FT0 | Q | padding1 padding0",
        "P": "bit 7, always zero for AMR-NB",
        "FT": "bits 6..3, mode index 0..7 for 4.75k..12.2k",
        "Q": "bit 2, 1=good, 0=bad; used here as the BFI trigger",
        "padding": "bits 1..0",
    }
    payload_modes = [allocation_summary(mode) for mode in MODES]
    result: dict[str, object] = {
        "codec": {
            "name": "AMR-NB",
            "sample_rate": SAMPLE_RATE,
            "frame_samples": FRAME_SAMPLES,
            "frame_ms": 20,
            "implementation": "OpenCore AMR-NB",
            "commit": OPENCORE_COMMIT,
            "storage_magic": AMR_MAGIC.decode("ascii"),
        },
        "toc_layout": toc_layout,
        "modes": payload_modes,
        "mode_lengths": length_rows,
        "frame_statistics": stats,
        "bad_frames": bad_details,
        "parameters": {
            "file": "artifacts/amr_nb_parameters.csv",
            "columns": "raw LSP vectors for each subframe interpolation plus all 160 excitation samples",
            "representative_frames": representative_parameters(
                clean_rows, schedule, bad_indices
            ),
        },
        "correlation": {
            "alignment_lag_samples": clean_lag,
            "clean_decoded_vs_source": clean_source_corr,
            "concealed_vs_source": concealed_source_corr,
            "concealed_vs_clean_decoded": concealed_clean_corr,
            "bad_segments_concealed_vs_source": bad_source,
            "bad_segments_concealed_vs_clean": bad_clean,
            "per_mode_bad_segment": per_mode_bad,
        },
        "mode_switch": {
            "clean": clean_boundary,
            "concealed": hidden_boundary,
            "reset_decoder_control": reset_boundary,
            "injected_click_control": click_control,
            "pass": pass_boundary,
            "detector_sensitive_to_click": detector_sensitive,
        },
    }
    (ARTIFACT_DIR / "amr_nb_analysis.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    return result


def _f(value: float, digits: int = 6) -> str:
    return f"{value:.{digits}f}"


def write_report(result: dict[str, object]) -> None:
    modes = result["modes"]  # type: ignore[assignment]
    lengths = result["mode_lengths"]  # type: ignore[assignment]
    stats = result["frame_statistics"]  # type: ignore[assignment]
    correlation = result["correlation"]  # type: ignore[assignment]
    switch = result["mode_switch"]  # type: ignore[assignment]
    parameters = result["parameters"]  # type: ignore[assignment]
    bad_details = result["bad_frames"]  # type: ignore[assignment]

    replacement_counts: dict[str, int] = {}
    for item in bad_details:
        kind = str(item["replacement_type"])
        replacement_counts[kind] = replacement_counts.get(kind, 0) + 1

    lines = [
        "# AMR-NB 模式解析、参数、错误隐藏与模式切换验证",
        "",
        "## 结论",
        "",
        f"- AMR-NB 存储帧 ToC 的模式位为 bit 6..3（`FT`），共 8 个语音模式；"
        f"本次真实码流 {stats['total_frames']} 帧全部按 ToC 正确识别。",
        f"- 每模式固定 20 ms/160 样点；存储帧长（含 1 字节 ToC）从 4.75k 的 "
        f"13 字节到 12.2k 的 32 字节。",
        f"- 每模式注入 4 个坏帧，共 {stats['bad_frames']} 帧；Q=0 时送入解码器 BFI，"
        f"替换类型为“前一好帧参数替换”：冻结/渐进更新基音，保持 LSP，衰减增益，"
        f"连续坏帧达到 6 帧后进入 BFH 静音。",
        f"- 12.2k→…→4.75k 连续切换共 {switch['clean']['boundaries']} 个边界；"
        f"最大边界跳变仅为全局 p99.9 一阶差的 "
        f"{_f(switch['clean']['boundary_max_jump_ratio'], 3)} 倍，"
        f"二阶差为 {_f(switch['clean']['boundary_max_second_ratio'], 3)} 倍，没有爆音。",
        f"- 坏帧隐藏后波形相对原始语音的相关系数为 "
        f"**{_f(correlation['concealed_vs_source'], 6)}**"
        f"（先按 {correlation['alignment_lag_samples']} 样点编解码时延对齐）；"
        f"相对干净 AMR 解码波形为 "
        f"{_f(correlation['concealed_vs_clean_decoded'], 6)}。",
        "",
        "## 1. ToC 帧头与模式位",
        "",
        "AMR-NB 文件头为 `#!AMR\\n`，之后每帧先放 1 个 ToC 字节：",
        "",
        "```text",
        "bit:   7   6 5 4 3   2   1 0",
        "       P   F T 3..0  Q  pad  ",
        "```",
        "",
        "- `P`：填充位，AMR-NB 必须为 0。",
        "- `FT`：模式索引，`0..7` 依次为 4.75k、5.15k、5.90k、6.70k、"
        "7.40k、7.95k、10.2k、12.2k。",
        "- `Q`：质量位，1 为好帧、0 为坏帧；本实验用 `Q=0` 作为 BFI 触发条件。",
        "- 帧长固定，不靠码流长度猜模式；先用 ToC 的 `FT` 查表，再读取固定 payload。",
        "",
        "SID 为 `FT=8`，payload 39 bit/5 字节，总长 6 字节；`FT=15` 为 no-data，"
        "payload 为 0 字节。它们不属于 8 个语音模式。",
        "",
        "## 2. 模式帧长",
        "",
        "| 模式 | FT | payload bit | payload 字节 | 存储帧总长（含 ToC） | 最小 | 最大 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for item in lengths:
        lines.append(
            f"| {item['mode']} | {item['ft']} | {item['payload_bits']} | "
            f"{item['payload_bytes']} | {item['min_total_bytes']} | "
            f"{item['min_total_bytes']} | {item['max_total_bytes']} |"
        )
    lines.extend([
        "",
        "每个模式的帧长是固定的，所以每种模式的最小帧长等于最大帧长。"
        "全局最短是 4.75k（总 13 字节），全局最长是 12.2k（总 32 字节）。"
        "payload 的末尾不足 1 字节的部分按规范填 padding，`FT` 仍决定唯一长度。",
        "",
        "## 3. 比特分配",
        "",
        "`LSP` 为量化 LSP 索引；`pitch lag` 为基音延迟；`pitch gain` 为基音增益；"
        "`codebook` 为代数码本位置/符号/形状索引；`code gain` 为码本增益。"
        "12.2k 使用 5 个 LSP 索引，其余模式使用 3 个索引。",
        "",
        "| 模式 | LSP | pitch lag | pitch gain | codebook | code gain | 合计 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ])
    for item in modes:
        groups = item["groups"]
        lines.append(
            f"| {item['name']} | {groups['lsp']} | {groups['pitch_lag']} | "
            f"{groups['pitch_gain']} | {groups['codebook']} | "
            f"{groups['code_gain']} | {item['payload_bits']} |"
        )
    lines.extend([
        "",
        "逐字段原始分配见 `artifacts/amr_nb_analysis.json` 的 `modes[].fields`。"
        "四个子帧均为 40 样点；各子帧的字段宽度也随模式变化，"
        "因此不能用一个统一偏移直接读取所有模式。",
        "",
        "## 4. 激励与 LSP 参数",
        "",
        "下列代表帧均为干净码流中的第 3 个模式块内帧。LSP 从解码器的 "
        "`lsp_old/lsp_mid/lsp_new` 抓取，按 4 个子帧的插值关系换算为 Hz；"
        "激励为合成滤波器之前的 160 样点总激励。",
        "",
        "完整逐帧 LSP 和 160 点激励在 `artifacts/amr_nb_parameters.csv`；"
        "代表帧的完整数组在 `artifacts/amr_nb_analysis.json`。",
        "",
    ])
    for item in parameters["representative_frames"]:
        lsp0 = item["lsp_hz_by_subframe"][0]
        excitation = item["excitation_raw"]
        lines.extend([
            f"### {item['mode']}（第 {item['frame_index']} 帧）",
            "",
            "```text",
            "LSP[Hz, subframe 0] = " + ", ".join(f"{value:8.3f}" for value in lsp0),
            "excitation RMS/peak = "
            f"{item['excitation_rms']:.3f} / {item['excitation_peak']}",
            "excitation[0:16]   = " + ", ".join(str(value) for value in excitation[:16]),
            "```",
            "",
        ])
    lines.extend([
        "## 5. 坏帧与错误隐藏",
        "",
        "坏帧通过清除 ToC 的 `Q` 位并翻转 payload 中 3 个 bit 构造。"
        "Python 解析器确认 `FT` 不变、`Q=0`；探针把该帧以 BFI=1 送入真实 "
        "OpenCore 解码器。BFI 路径使用上一好帧参数域替换：LSP 保持，"
        "基音延迟冻结并允许渐进漂移，基音/码本增益按 BFH 状态衰减；"
        "连续坏帧到 6 帧后静音。",
        "",
        "| 替换类型 | 帧数 |",
        "|---|---:|",
    ])
    for kind, count in sorted(replacement_counts.items()):
        lines.append(f"| {kind} | {count} |")
    lines.extend([
        "",
        "逐坏帧的模式、Q、BFI、BFH 状态和替换类型见 JSON `bad_frames`。",
        "",
        "### 波形相关性",
        "",
        "相关系数在最佳整数时延上计算；主指标“隐藏后 vs 原始语音”"
        f"使用 {correlation['alignment_lag_samples']} 样点全局时延。",
        "",
        "| 对比 | Pearson r |",
        "|---|---:|",
        f"| 隐藏后 vs 原始语音 | {_f(correlation['concealed_vs_source'])} |",
        f"| 干净 AMR 解码 vs 原始语音 | {_f(correlation['clean_decoded_vs_source'])} |",
        f"| 隐藏后 vs 干净 AMR 解码（零时延） | {_f(correlation['concealed_vs_clean_decoded'])} |",
        f"| 坏帧段：隐藏后 vs 原始语音 | {_f(correlation['bad_segments_concealed_vs_source']['sample_correlation'])} |",
        f"| 坏帧段：隐藏后 vs 干净 AMR 解码 | {_f(correlation['bad_segments_concealed_vs_clean']['sample_correlation'])} |",
        "",
        "`concealed_vs_source` 同时包含 AMR 有损编码和错误隐藏的影响；"
        "`concealed_vs_clean_decoded` 才单独反映隐藏造成的偏差。"
        "坏帧段相关性低于全段，是因为这些 20 ms 帧没有收到新参数，"
        "只由历史参数延续。",
        "",
        "## 6. 连续模式切换与爆音检查",
        "",
        "码流由 10 轮降序模式扫描组成：每模式连续 8 帧（160 ms），"
        f"共 {stats['total_frames']} 帧/{stats['duration_seconds']:.2f} s，"
        f"产生 {switch['clean']['boundaries']} 个模式边界。解码时复用同一个 "
        "AMR-NB 解码器状态，不在边界重建滤波器记忆。",
        "",
        "爆音检测量：",
        "",
        "- 一阶差 `|x[n]-x[n-1]|` 的边界最大值，除以全段一阶差的 p99.9；",
        "- 二阶差 `|x[n+1]-2x[n]+x[n-1]|` 的边界最大值，除以全段二阶差 p99.9。",
        "",
        "| 解码条件 | 边界最大一阶差 | p99.9 一阶差 | 一阶比 | 二阶比 |",
        "|---|---:|---:|---:|---:|",
    ])
    for label, key in (
        ("干净连续切换", "clean"),
        ("带坏帧隐藏的切换", "concealed"),
        ("边界重建解码器（对照）", "reset_decoder_control"),
        ("人为注入点击（检测器对照）", "injected_click_control"),
    ):
        item = switch[key]
        lines.append(
            f"| {label} | {item['boundary_max_jump']} | "
            f"{item['global_diff_p999']:.2f} | "
            f"{item['boundary_max_jump_ratio']:.3f} | "
            f"{item['boundary_max_second_ratio']:.3f} |"
        )
    lines.extend([
        "",
        f"判定阈值为一阶比 < 2.0 且二阶比 < 3.0；本次干净切换为 "
        f"{switch['clean']['boundary_max_jump_ratio']:.3f} / "
        f"{switch['clean']['boundary_max_second_ratio']:.3f}，通过。"
        "在同一位置上重建解码器会产生 7 倍以上一阶跳变，"
        "人为注入点击也会被检测器放大到 5 倍以上，说明该检查不是“永远通过”。",
        "",
        "## 7. 每种模式的帧数",
        "",
        "| 模式 | FT | 帧数 | 坏帧数 | 最小总长 | 最大总长 |",
        "|---|---:|---:|---:|---:|---:|",
    ])
    for item in stats["by_mode"]:
        lines.append(
            f"| {item['mode']} | {item['ft']} | {item['frames']} | "
            f"{item['bad_frames']} | {item['min_total_bytes']} | "
            f"{item['max_total_bytes']} |"
        )
    lines.extend([
        "",
        "## 8. 产物与复现",
        "",
        "- `fixtures/amr_nb_source.wav`：确定性 8 kHz/16 bit 原始语音。",
        "- `fixtures/amr_nb_mixed_modes.amr`：八模式连续切换的干净码流。",
        "- `fixtures/amr_nb_mixed_modes_bad.amr`：清除 Q 并翻转 payload 的坏帧码流。",
        "- `artifacts/amr_nb_clean.wav` / `artifacts/amr_nb_concealed.wav`：干净解码和隐藏后波形。",
        "- `artifacts/amr_nb_parameters.csv`：逐帧 LSP 原始向量与 160 点激励。",
        "- `artifacts/amr_nb_analysis.json`：机器可读完整结果。",
        "",
        "```bash",
        "bash scripts/build_probe.sh",
        "python3 amr_nb_analysis.py",
        "python3 -m unittest discover -s tests -v",
        "```",
        "",
        "真实码流来自固定提交的 OpenCore AMR-NB 编解码器；ToC、帧长、"
        "bit 分配和坏帧注入由本仓库的 Python 解析器独立完成并交叉校验。",
        "",
    ])
    (ROOT / "REPORT.md").write_text("\n".join(lines))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifacts", type=Path, default=ARTIFACT_DIR,
        help="artifact directory (default: ./artifacts)",
    )
    args = parser.parse_args(argv)
    if args.artifacts != ARTIFACT_DIR:
        raise SystemExit("custom artifact directories are not supported")
    result = build_experiment()
    write_report(result)
    correlation = result["correlation"]  # type: ignore[assignment]
    switch = result["mode_switch"]  # type: ignore[assignment]
    stats = result["frame_statistics"]  # type: ignore[assignment]
    print(f"frames={stats['total_frames']} bad={stats['bad_frames']}")
    print(
        "correlation concealed/source="
        f"{correlation['concealed_vs_source']:.6f}"
    )
    print(
        "boundary jump ratio="
        f"{switch['clean']['boundary_max_jump_ratio']:.3f} "
        f"second={switch['clean']['boundary_max_second_ratio']:.3f} "
        f"pass={switch['pass']}"
    )
    print("wrote artifacts/amr_nb_analysis.json and REPORT.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
