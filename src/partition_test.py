#!/usr/bin/env python3
"""Lossless impulse-response checks for two partition layouts."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from common import FFMPEG, RESULTS, WORK, run, sha256_bytes, write_json

SIZE = 128
TOLERANCE_MAX_ABS = 1
TOLERANCE_MEAN_ABS = 0.01


def make_impulse_yuv() -> bytes:
    y = np.full((SIZE, SIZE), 16, dtype=np.uint8)
    impulses = (
        (64, 64, 220),
        (32, 64, 170),
        (64, 32, 140),
        (96, 96, 120),
        (64, 0, 90),
        (0, 64, 80),
    )
    yy, xx = np.mgrid[0:SIZE, 0:SIZE]
    for cx, cy, amplitude in impulses:
        y[cy, cx] = min(255, int(y[cy, cx]) + amplitude)
        y = np.maximum(
            y,
            np.clip(
                amplitude
                * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * 1.25**2)),
                0,
                255,
            ).astype(np.uint8),
        )
    chroma = np.full((SIZE // 2, SIZE // 2), 128, dtype=np.uint8)
    return y.tobytes() + chroma.tobytes() + chroma.tobytes()


def decode_raw(path: Path) -> bytes:
    result = run(
        [
            str(FFMPEG), "-hide_banner", "-loglevel", "error",
            "-i", str(path), "-pix_fmt", "yuv420p",
            "-f", "rawvideo", "-",
        ],
        capture_stdout=True,
    )
    return result.stdout


def compare(reference: bytes, candidate: bytes) -> dict[str, float | str]:
    a = np.frombuffer(reference, dtype=np.uint8).astype(np.int16)
    b = np.frombuffer(candidate, dtype=np.uint8).astype(np.int16)
    if a.shape != b.shape:
        raise ValueError("raw frame sizes differ")
    difference = np.abs(a - b)
    mse = float(np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2))
    psnr: float | str = "inf" if mse == 0 else 10 * math.log10(255**2 / mse)
    return {
        "samples": int(a.size),
        "max_abs_diff_lsb": int(difference.max()),
        "mean_abs_diff_lsb": float(difference.mean()),
        "mismatched_samples": int(np.count_nonzero(difference)),
        "psnr_db": psnr,
    }


def encode_x265(source_yuv: Path, output: Path, tiles: str) -> None:
    run(
        [
            str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{SIZE}x{SIZE}",
            "-r", "1", "-i", str(source_yuv),
            "-c:v", "libx265", "-preset", "medium", "-qp", "0",
            "-x265-params",
            f"lossless=1:tiles={tiles}:pools=1:frame-threads=1:log-level=error",
            "-an", "-f", "hevc", str(output),
        ]
    )


def encode_x264(source_yuv: Path, output: Path, partitions: str) -> None:
    run(
        [
            str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{SIZE}x{SIZE}",
            "-r", "1", "-i", str(source_yuv),
            "-c:v", "libx264", "-preset", "medium", "-qp", "0",
            "-x264-params", f"partitions={partitions}:threads=1",
            "-an", "-f", "h264", str(output),
        ]
    )


def main() -> None:
    work = WORK / "partition"
    work.mkdir(parents=True, exist_ok=True)
    source_data = make_impulse_yuv()
    source_yuv = work / "impulse_128x128.yuv"
    source_yuv.write_bytes(source_data)

    x265_a = work / "x265_tiles_1x1.hevc"
    x265_b = work / "x265_tiles_2x2.hevc"
    encode_x265(source_yuv, x265_a, "1x1")
    encode_x265(source_yuv, x265_b, "2x2")
    raw_a = decode_raw(x265_a)
    raw_b = decode_raw(x265_b)

    x264_a = work / "x264_partitions_none.h264"
    x264_b = work / "x264_partitions_all.h264"
    encode_x264(source_yuv, x264_a, "none")
    encode_x264(source_yuv, x264_b, "all")
    raw_x264_a = decode_raw(x264_a)
    raw_x264_b = decode_raw(x264_b)

    tests = {
        "x265_tiles": {
            "partitions": ["1x1", "2x2"],
            "output_a": str(x265_a.relative_to(x265_a.parents[2])),
            "output_b": str(x265_b.relative_to(x265_b.parents[2])),
            "a_vs_b": compare(raw_a, raw_b),
            "a_vs_source": compare(source_data, raw_a),
            "b_vs_source": compare(source_data, raw_b),
        },
        "x264_macroblock_partitions": {
            "partitions": ["none", "all"],
            "output_a": str(x264_a.relative_to(x264_a.parents[2])),
            "output_b": str(x264_b.relative_to(x264_b.parents[2])),
            "a_vs_b": compare(raw_x264_a, raw_x264_b),
            "a_vs_source": compare(source_data, raw_x264_a),
            "b_vs_source": compare(source_data, raw_x264_b),
        },
    }
    for result in tests.values():
        differences = [result["a_vs_b"], result["a_vs_source"], result["b_vs_source"]]
        result["tolerance"] = {
            "max_abs_diff_lsb": TOLERANCE_MAX_ABS,
            "mean_abs_diff_lsb": TOLERANCE_MEAN_ABS,
        }
        result["within_tolerance"] = all(
            item["max_abs_diff_lsb"] <= TOLERANCE_MAX_ABS
            and item["mean_abs_diff_lsb"] <= TOLERANCE_MEAN_ABS
            for item in differences
        )
    payload = {
        "source": {
            "size": f"{SIZE}x{SIZE}",
            "pix_fmt": "yuv420p",
            "sha256": sha256_bytes(source_data),
            "impulse": "Gaussian impulses including tile-boundary locations",
        },
        "tests": tests,
    }
    write_json(RESULTS / "partition_impulse.json", payload)
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
