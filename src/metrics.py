#!/usr/bin/env python3
"""Quality, high-frequency-detail, and buffer metrics."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from common import FFPROBE, FFMPEG, FPS, FRAMES, HEIGHT, WIDTH, run


def _parse_ssim_stats(path: Path) -> list[dict[str, float]]:
    rows: list[dict[str, float]] = []
    for line in path.read_text().splitlines():
        values: dict[str, float] = {}
        for token in line.split():
            key, _, value = token.partition(":")
            if key == "n":
                values["frame"] = float(value)
            elif key in {"Y", "U", "V", "All"}:
                values[key] = float(value)
        if values:
            rows.append(values)
    return rows


def measure_ssim(
    source: Path, encoded: Path, stats_path: Path
) -> tuple[dict[str, float], list[dict[str, float]]]:
    stats_path.parent.mkdir(parents=True, exist_ok=True)
    result = run(
        [
            str(FFMPEG), "-hide_banner", "-loglevel", "info",
            "-i", str(source), "-i", str(encoded),
            "-lavfi", f"[0:v][1:v]ssim=stats_file={stats_path}",
            "-f", "null", "-",
        ]
    )
    rows = _parse_ssim_stats(stats_path)
    if len(rows) != FRAMES:
        raise RuntimeError(f"SSIM produced {len(rows)} frames, expected {FRAMES}")
    summary = {
        "ssim_y": float(np.mean([row["Y"] for row in rows])),
        "ssim_all": float(np.mean([row["All"] for row in rows])),
        "ssim_min": float(np.min([row["All"] for row in rows])),
        "ssim_std": float(np.std([row["All"] for row in rows])),
    }
    if "not found" in result.stderr.lower() or "error" in result.stderr.lower():
        pass
    return summary, rows


def decode_luma(path: Path) -> np.ndarray:
    result = run(
        [
            str(FFMPEG), "-hide_banner", "-loglevel", "error",
            "-i", str(path), "-pix_fmt", "yuv420p",
            "-f", "rawvideo", "-",
        ],
        capture_stdout=True,
    )
    frame_size = WIDTH * HEIGHT * 3 // 2
    expected = FRAMES * frame_size
    if len(result.stdout) != expected:
        raise RuntimeError(f"decoded {len(result.stdout)} bytes, expected {expected}")
    planes = np.frombuffer(result.stdout, dtype=np.uint8).reshape(FRAMES, frame_size)
    return planes[:, : WIDTH * HEIGHT].reshape(FRAMES, HEIGHT, WIDTH).astype(np.float64)


def decoded_md5(path: Path) -> str:
    result = run(
        [
            str(FFMPEG), "-hide_banner", "-loglevel", "error",
            "-i", str(path), "-map", "0:v:0", "-f", "md5", "-",
        ],
        capture_stdout=True,
    )
    return result.stdout.decode("ascii").strip().removeprefix("MD5=")


def _high_pass(frame: np.ndarray) -> np.ndarray:
    padded = np.pad(frame, 1, mode="edge")
    neighbor_mean = (
        padded[:-2, 1:-1]
        + padded[2:, 1:-1]
        + padded[1:-1, :-2]
        + padded[1:-1, 2:]
    ) / 4.0
    return frame - neighbor_mean


def measure_high_frequency(source: Path, encoded: Path) -> tuple[dict[str, float], list[dict[str, float]]]:
    source_luma = decode_luma(source)
    encoded_luma = decode_luma(encoded)
    rows: list[dict[str, float]] = []
    for index, (reference, distorted) in enumerate(zip(source_luma, encoded_luma)):
        reference_hp = _high_pass(reference)
        distorted_hp = _high_pass(distorted)
        source_energy = float(np.mean(reference_hp**2))
        encoded_energy = float(np.mean(distorted_hp**2))
        ratio = encoded_energy / max(source_energy, 1e-12)
        reference_flat = reference_hp.ravel()
        distorted_flat = distorted_hp.ravel()
        if np.std(reference_flat) > 1e-9 and np.std(distorted_flat) > 1e-9:
            correlation = float(np.corrcoef(reference_flat, distorted_flat)[0, 1])
        else:
            correlation = 1.0
        relative_hf_error = float(
            np.mean(np.abs(reference_hp - distorted_hp)) / max(np.sqrt(source_energy), 1e-12)
        )
        rows.append(
            {
                "frame": index,
                "hf_energy_ratio": ratio,
                "hf_gain_db": 10 * math.log10(max(ratio, 1e-12)),
                "hf_correlation": correlation,
                "relative_hf_error": relative_hf_error,
            }
        )
    summary = {
        key: float(np.mean([row[key] for row in rows]))
        for key in ("hf_energy_ratio", "hf_gain_db", "hf_correlation", "relative_hf_error")
    }
    return summary, rows


def frame_packets(path: Path) -> list[dict[str, object]]:
    result = run(
        [
            str(FFPROBE), "-v", "error", "-select_streams", "v:0",
            "-show_frames", "-show_entries", "frame=pkt_size,pict_type,pts_time",
            "-of", "json", str(path),
        ],
        capture_stdout=True,
    )
    payload = json.loads(result.stdout)
    rows = []
    for index, frame in enumerate(payload["frames"]):
        rows.append(
            {
                "frame": index,
                "bits": int(frame["pkt_size"]) * 8,
                "type": frame.get("pict_type", "?"),
                "pts_time": float(frame.get("pts_time", index / FPS)),
            }
        )
    if len(rows) != FRAMES:
        raise RuntimeError(f"packet parser produced {len(rows)} frames, expected {FRAMES}")
    return rows


def buffer_model(
    packets: list[dict[str, object]],
    achieved_kbps: float,
    buffer_seconds: float = 0.5,
) -> tuple[dict[str, object], list[dict[str, float]]]:
    capacity = achieved_kbps * 1000 * buffer_seconds
    level = capacity
    rows: list[dict[str, float]] = []
    violations: list[int] = []
    minimum = capacity
    for index, packet in enumerate(packets):
        arrival = achieved_kbps * 1000 / FPS
        before = min(capacity, level + arrival)
        bits = float(packet["bits"])
        if before + 1e-9 < bits:
            violations.append(index)
        after = before - bits
        minimum = min(minimum, after)
        level = max(0.0, min(capacity, after))
        rows.append(
            {
                "frame": index,
                "arrival_bits": arrival,
                "frame_bits": bits,
                "buffer_before_bits": before,
                "buffer_after_bits": level,
                "underflow_bits": max(0.0, bits - before),
            }
        )
    summary = {
        "capacity_bits": capacity,
        "buffer_seconds": buffer_seconds,
        "initial_fill": 1.0,
        "violations": len(violations),
        "violation_frames": violations,
        "min_buffer_bits": minimum,
        "first_frame_violation": 0 in violations,
    }
    return summary, rows


def allocation_summary(
    packets: list[dict[str, object]], achieved_kbps: float
) -> tuple[dict[str, object], list[dict[str, float]]]:
    bits = np.asarray([float(row["bits"]) for row in packets])
    types = [str(row["type"]) for row in packets]
    total = float(bits.sum())
    by_type: dict[str, dict[str, float]] = {}
    for pict_type in sorted(set(types)):
        selected = bits[np.asarray([value == pict_type for value in types])]
        by_type[pict_type] = {
            "frames": int(selected.size),
            "total_bits": float(selected.sum()),
            "mean_bits": float(selected.mean()),
            "fraction": float(selected.sum() / total),
        }
    p95 = float(np.percentile(bits, 95))
    summary = {
        "achieved_kbps": achieved_kbps,
        "total_bits": total,
        "mean_frame_bits": float(bits.mean()),
        "std_frame_bits": float(bits.std()),
        "coefficient_of_variation": float(bits.std() / bits.mean()),
        "p95_over_mean": p95 / float(bits.mean()),
        "max_over_mean": float(bits.max() / bits.mean()),
        "by_frame_type": by_type,
    }
    rows = [
        {
            "frame": index,
            "bits": float(packet["bits"]),
            "kbps": float(packet["bits"]) * FPS / 1000,
            "type": types[index],
            "normalized_bits": float(packet["bits"]) / float(bits.mean()),
        }
        for index, packet in enumerate(packets)
    ]
    return summary, rows
