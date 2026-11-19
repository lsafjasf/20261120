"""CSV / JSON persistence and ffprobe-based bitrate measurement."""

from __future__ import annotations

import csv
import json
import subprocess

import numpy as np

from .pipeline import METRICS


def write_per_frame_csv(
    path: str, rows: list[dict], noise_flags: np.ndarray
) -> None:
    fieldnames = [
        "frame", "time_sec", "segment", "psnr_db", "ssim", "msssim",
        "is_high_noise",
    ]
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            record = dict(row)
            record["is_high_noise"] = int(noise_flags[row["frame"]])
            writer.writerow(record)


def write_csv(path: str, rows: list[dict]) -> None:
    if not rows:
        return
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: str, payload: dict) -> None:
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=2)


def measure_segment_bitrates(
    video_path: str, fps: float, boundary_frame: int
) -> dict:
    """Measure actual packet payload bytes per segment with ffprobe."""
    command = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "packet=pts_time,size",
        "-of", "json", video_path,
    ]
    output = subprocess.run(
        command, check=True, capture_output=True, text=True
    ).stdout
    packets = json.loads(output)["packets"]
    sizes = [int(packet["size"]) for packet in packets]
    boundary_time = boundary_frame / fps
    times = [
        float(packet["pts_time"]) if packet.get("pts_time") not in (None, "N/A")
        else index / fps
        for index, packet in enumerate(packets)
    ]
    high_bytes = sum(
        size for size, ts in zip(sizes, times) if ts < boundary_time
    )
    low_bytes = sum(sizes) - high_bytes
    high_seconds = boundary_frame / fps
    low_seconds = (len(sizes) - boundary_frame) / fps
    high_kbps = high_bytes * 8 / high_seconds / 1000.0
    low_kbps = low_bytes * 8 / low_seconds / 1000.0
    return {
        "measurement": "actual MP4 packet payload bytes via ffprobe",
        "high": {
            "frames": [0, boundary_frame],
            "kbit_per_second": round(high_kbps, 3),
            "payload_bytes": high_bytes,
        },
        "low": {
            "frames": [boundary_frame, len(sizes)],
            "kbit_per_second": round(low_kbps, 3),
            "payload_bytes": low_bytes,
        },
        "ratio": high_kbps / low_kbps,
    }


def segment_means(
    rows: list[dict], boundary_frame: int
) -> list[dict]:
    values = {
        metric: np.asarray([row[metric] for row in rows], dtype=np.float64)
        for metric in METRICS
    }
    segments = [
        ("high", 0, boundary_frame),
        ("low", boundary_frame, len(rows)),
    ]
    output = []
    for label, start, end in segments:
        output.append(
            {
                "label": label,
                "start": start,
                "end": end,
                "frame_count": end - start,
                "means": {
                    metric: float(values[metric][start:end].mean())
                    for metric in METRICS
                },
            }
        )
    return output
