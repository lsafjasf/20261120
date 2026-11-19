#!/usr/bin/env python3
"""Generate deterministic WavPack fixtures used by the scanner tests."""

from __future__ import annotations

import argparse
import math
import shutil
import struct
import subprocess
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wavpack_blocks import parse_header  # noqa: E402


FRAME_COUNT = 2200
BLOCK_SIZE = 512
SAMPLE_RATE = 44100


def samples() -> tuple[list[int], list[int], list[int]]:
    left: list[int] = []
    right: list[int] = []
    mono: list[int] = []
    for index in range(FRAME_COUNT):
        left_sample = round(
            12000 * math.sin(2 * math.pi * 440 * index / SAMPLE_RATE)
        )
        right_sample = round(
            8000 * math.sin(2 * math.pi * 660 * index / SAMPLE_RATE)
        )
        left.append(left_sample)
        right.append(right_sample)
        mono.append((left_sample + right_sample) // 2)
    return mono, left, right


def write_wav(path: Path, channels: int, channel_data: list[list[int]]) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        frames = bytearray()
        for index in range(FRAME_COUNT):
            for channel in range(channels):
                frames += struct.pack("<h", channel_data[channel][index])
        output.writeframes(frames)


def run(command: list[str], check: bool = True) -> subprocess.CompletedProcess:
    completed = subprocess.run(command, text=True, capture_output=True)
    if check and completed.returncode:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(command)}\n"
            f"{completed.stdout}{completed.stderr}"
        )
    return completed


def encode(
    wavpack: Path, source: Path, output: Path, extra: list[str] | None = None
) -> None:
    command = [
        str(wavpack), "-q", "-y", f"--blocksize={BLOCK_SIZE}",
        *(extra or []), str(source), "-o", str(output),
    ]
    run(command)


def decode_raw(wvunpack: Path, source: Path, output: Path) -> None:
    output.unlink(missing_ok=True)
    completed = run(
        [str(wvunpack), "-q", "-y", "--raw", "-o", str(output), str(source)],
        check=False,
    )
    if not output.exists():
        raise RuntimeError(
            f"could not decode {source}:\n{completed.stdout}{completed.stderr}"
        )


def corrupt_middle_payload(data: bytes) -> tuple[bytes, int]:
    offsets: list[int] = []
    cursor = 0
    while cursor < len(data):
        header = parse_header(data, cursor)
        offsets.append(cursor)
        cursor = header.declared_end
    middle_offset = offsets[len(offsets) // 2]
    damaged = bytearray(data)
    damage_start = middle_offset + 96
    damage_stop = min(middle_offset + 152, len(damaged))
    damaged[damage_start:damage_stop] = b"\xA5" * (damage_stop - damage_start)
    return bytes(damaged), middle_offset


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wavpack", default=shutil.which("wavpack"))
    parser.add_argument("--wvunpack", default=shutil.which("wvunpack"))
    parser.add_argument(
        "--output", type=Path, default=ROOT / "fixtures"
    )
    args = parser.parse_args()
    if not args.wavpack or not args.wvunpack:
        parser.error("wavpack and wvunpack must be installed or passed explicitly")

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    source = output / "_source"
    source.mkdir(exist_ok=True)

    mono, left, right = samples()
    write_wav(source / "mono.wav", 1, [mono])
    write_wav(source / "stereo.wav", 2, [left, right])

    encode(Path(args.wavpack), source / "mono.wav", output / "mono.wv")
    encode(Path(args.wavpack), source / "stereo.wav", output / "stereo.wv")
    encode(
        Path(args.wavpack), source / "stereo.wav", output / "hybrid.wv",
        ["-b3"],
    )

    for name in ("mono", "stereo", "hybrid"):
        decode_raw(
            Path(args.wvunpack),
            output / f"{name}.wv",
            output / f"{name}.decoded.raw",
        )

    clean = (output / "stereo.wv").read_bytes()
    damaged, corrupted_start = corrupt_middle_payload(clean)
    (output / "corrupt_stereo.wv").write_bytes(damaged)
    decode_raw(
        Path(args.wvunpack),
        output / "corrupt_stereo.wv",
        output / "corrupt_stereo.decoded.raw",
    )

    broken_length = bytearray(damaged)
    struct.pack_into("<I", broken_length, corrupted_start + 4, 0xFFFFFFF0)
    (output / "corrupt_length_stereo.wv").write_bytes(broken_length)
    decode_raw(
        Path(args.wvunpack),
        output / "corrupt_length_stereo.wv",
        output / "corrupt_length_stereo.decoded.raw",
    )

    shutil.rmtree(source)
    print(f"wrote fixtures to {output}")
    print(f"damaged block starts at byte 0x{corrupted_start:X}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
