#!/usr/bin/env python3
"""Run the reproducible x264/x265 RD, timing, allocation, and buffer study."""

from __future__ import annotations

import argparse
import csv
import json
import platform
import statistics
from pathlib import Path

import numpy as np

from common import (
    ARTIFACTS,
    ENCODED,
    ENCODERS,
    FFMPEG,
    FIGURES,
    FRAMES,
    MEDIA,
    PRIMARY_KBPS,
    REPEATS,
    RESULTS,
    ROOT,
    SWEEP_KBPS,
    TIMING_REPEATS,
    WORK,
    ensure_dirs,
    read_json,
    run,
    sha256_file,
    write_json,
)
from encode import calibrate_rate, encode_twopass
from metrics import (
    allocation_summary,
    buffer_model,
    decoded_md5,
    frame_packets,
    measure_high_frequency,
    measure_ssim,
)

CLIPS = ("static", "motion")


def collect_cell(encoder: str, clip: str, desired_kbps: int) -> dict[str, object]:
    source = MEDIA / f"{clip}_640x360_30.yuv"
    reference = MEDIA / f"{clip}.mkv"
    calibration = calibrate_rate(encoder, clip, source, desired_kbps)
    output = ROOT / calibration["output"]
    stem = f"{encoder}_{clip}_{desired_kbps}k"
    time_runs = [float(calibration["elapsed_s"])]
    cpu_runs = [float(calibration["cpu_s"])]
    rate_runs = [float(calibration["achieved_kbps"])]
    timing_outputs = [
        {
            "run": 1,
            "path": output,
            "wall_s": time_runs[0],
            "cpu_s": cpu_runs[0],
            "achieved_kbps": rate_runs[0],
        }
    ]
    for timing_index in range(2, TIMING_REPEATS + 1):
        timing_output = (
            WORK / "timing" / stem / f"run_{timing_index}{ENCODERS[encoder]['extension']}"
        )
        elapsed_s, cpu_s, achieved_kbps = encode_twopass(
            encoder,
            source,
            timing_output,
            int(calibration["target_kbps"]),
            timing_output.parent / f"stats_{timing_index}",
        )
        time_runs.append(elapsed_s)
        cpu_runs.append(cpu_s)
        rate_runs.append(achieved_kbps)
        timing_outputs.append(
            {
                "run": timing_index,
                "path": timing_output,
                "wall_s": elapsed_s,
                "cpu_s": cpu_s,
                "achieved_kbps": achieved_kbps,
            }
        )
    calibration["elapsed_s"] = statistics.median(time_runs)
    calibration["cpu_s"] = statistics.median(cpu_runs)
    calibration["timing"] = {
        "runs_s": time_runs,
        "cpu_runs_s": cpu_runs,
        "achieved_kbps_runs": rate_runs,
        "mean_s": statistics.mean(time_runs),
        "median_s": statistics.median(time_runs),
        "min_s": min(time_runs),
        "std_s": statistics.pstdev(time_runs),
        "cv": statistics.pstdev(time_runs) / statistics.mean(time_runs),
        "cpu_mean_s": statistics.mean(cpu_runs),
        "cpu_median_s": statistics.median(cpu_runs),
        "cpu_min_s": min(cpu_runs),
        "cpu_std_s": statistics.pstdev(cpu_runs),
        "cpu_cv": statistics.pstdev(cpu_runs) / statistics.mean(cpu_runs),
    }
    ssim_summary, ssim_rows = measure_ssim(reference, output, WORK / "ssim" / f"{stem}.log")
    hf_summary, hf_rows = measure_high_frequency(reference, output)
    packets = frame_packets(output)
    buffer_summary, buffer_rows = buffer_model(packets, calibration["achieved_kbps"])
    allocation, allocation_rows = allocation_summary(packets, calibration["achieved_kbps"])
    calibration.update(
        {
            "ssim": ssim_summary,
            "high_frequency": hf_summary,
            "buffer": buffer_summary,
            "allocation": allocation,
            "decoded_md5": decoded_md5(output),
            "ssim_frames": ssim_rows,
            "hf_frames": hf_rows,
            "allocation_frames": allocation_rows,
            "buffer_frames": buffer_rows,
            "timing_runs": [
                {
                    "run": item["run"],
                    "path": str(item["path"].relative_to(ROOT)),
                    "wall_s": item["wall_s"],
                    "cpu_s": item["cpu_s"],
                    "achieved_kbps": item["achieved_kbps"],
                    "sha256": sha256_file(item["path"]),
                    "decoded_md5": decoded_md5(item["path"]),
                }
                for item in timing_outputs
            ],
        }
    )
    print(
        f"{encoder:4s} {clip:6s} {desired_kbps:3d}k -> "
        f"{calibration['achieved_kbps']:7.2f} kbps, "
        f"SSIM={ssim_summary['ssim_all']:.5f}, "
        f"HF={hf_summary['hf_energy_ratio']:.4f}, "
        f"buffer={buffer_summary['violations']}, "
        f"cpu={calibration['cpu_s']:.3f}s "
        f"(wall={calibration['elapsed_s']:.3f}s)"
    )
    return calibration


def build_reproducibility(cells: list[dict[str, object]]) -> dict[str, object]:
    summary: dict[str, object] = {}
    for cell in cells:
        if int(cell["desired_kbps"]) != PRIMARY_KBPS:
            continue
        key = f"{cell['encoder']}_{cell['clip']}"
        reference = MEDIA / f"{cell['clip']}.mkv"
        runs = []
        for run in cell["timing_runs"]:
            output = ROOT / str(run["path"])
            ssim_summary, _ = measure_ssim(
                reference,
                output,
                WORK / "reproducibility" / f"{key}_{run['run']}.log",
            )
            hf_summary, _ = measure_high_frequency(reference, output)
            runs.append(
                {
                    "repeat": run["run"],
                    "sha256": run["sha256"],
                    "decoded_md5": run["decoded_md5"],
                    "achieved_kbps": run["achieved_kbps"],
                    "elapsed_s": run["wall_s"],
                    "cpu_s": run["cpu_s"],
                    "ssim_all": ssim_summary["ssim_all"],
                    "hf_energy_ratio": hf_summary["hf_energy_ratio"],
                }
            )
        times = [float(item["elapsed_s"]) for item in runs]
        cpu_times = [float(item["cpu_s"]) for item in runs]
        summary[key] = {
            "runs": runs,
            "bitstream_identical": len({item["sha256"] for item in runs}) == 1,
            "decoded_identical": len({item["decoded_md5"] for item in runs}) == 1,
            "ssim_range": max(item["ssim_all"] for item in runs)
            - min(item["ssim_all"] for item in runs),
            "hf_range": max(item["hf_energy_ratio"] for item in runs)
            - min(item["hf_energy_ratio"] for item in runs),
            "time_mean_s": statistics.mean(times),
            "time_std_s": statistics.pstdev(times),
            "time_cv": statistics.pstdev(times) / statistics.mean(times),
            "cpu_time_mean_s": statistics.mean(cpu_times),
            "cpu_time_std_s": statistics.pstdev(cpu_times),
            "cpu_time_cv": statistics.pstdev(cpu_times) / statistics.mean(cpu_times),
        }
    return summary


def primary_map(cells: list[dict[str, object]]) -> dict[tuple[str, str], dict[str, object]]:
    return {
        (str(cell["encoder"]), str(cell["clip"])): cell
        for cell in cells
        if int(cell["desired_kbps"]) == PRIMARY_KBPS
    }


def build_comparisons(cells: list[dict[str, object]]) -> dict[str, object]:
    primary = primary_map(cells)
    clips: dict[str, object] = {}
    for clip in CLIPS:
        x264 = primary[("x264", clip)]
        x265 = primary[("x265", clip)]
        rate_delta = abs(float(x264["achieved_kbps"]) - float(x265["achieved_kbps"]))
        rate_delta_pct = rate_delta / float(x264["achieved_kbps"]) * 100
        clips[clip] = {
            "x264_kbps": x264["achieved_kbps"],
            "x265_kbps": x265["achieved_kbps"],
            "rate_difference_pct": rate_delta_pct,
            "ssim_all": {
                "x264": x264["ssim"]["ssim_all"],
                "x265": x265["ssim"]["ssim_all"],
                "delta_x265_minus_x264": x265["ssim"]["ssim_all"]
                - x264["ssim"]["ssim_all"],
            },
            "ssim_y": {
                "x264": x264["ssim"]["ssim_y"],
                "x265": x265["ssim"]["ssim_y"],
                "delta_x265_minus_x264": x265["ssim"]["ssim_y"]
                - x264["ssim"]["ssim_y"],
            },
            "encode_time_s": {
                "x264": x264["elapsed_s"],
                "x265": x265["elapsed_s"],
                "x265_over_x264": x265["elapsed_s"] / x264["elapsed_s"],
            },
            "encode_cpu_s": {
                "x264": x264["cpu_s"],
                "x265": x265["cpu_s"],
                "x265_over_x264": x265["cpu_s"] / x264["cpu_s"],
            },
            "buffer_violations": {
                "x264": x264["buffer"]["violations"],
                "x265": x265["buffer"]["violations"],
            },
            "high_frequency": {
                "x264": x264["high_frequency"],
                "x265": x265["high_frequency"],
                "energy_ratio_delta_x265_minus_x264": x265["high_frequency"][
                    "hf_energy_ratio"
                ]
                - x264["high_frequency"]["hf_energy_ratio"],
                "correlation_delta_x265_minus_x264": x265["high_frequency"][
                    "hf_correlation"
                ]
                - x264["high_frequency"]["hf_correlation"],
            },
            "allocation": {
                "x264": x264["allocation"],
                "x265": x265["allocation"],
            },
        }

    x265_hf_higher = all(
        clips[clip]["high_frequency"]["energy_ratio_delta_x265_minus_x264"] > 0
        for clip in CLIPS
    )
    x265_hf_corr_higher = all(
        clips[clip]["high_frequency"]["correlation_delta_x265_minus_x264"] > 0
        for clip in CLIPS
    )
    return {
        "operating_point_kbps": PRIMARY_KBPS,
        "clips": clips,
        "max_rate_difference_pct": max(
            clips[clip]["rate_difference_pct"] for clip in CLIPS
        ),
        "same_rate_within_one_percent": max(
            clips[clip]["rate_difference_pct"] for clip in CLIPS
        )
        <= 1.0,
        "x265_hf_energy_higher_on_both_clips": x265_hf_higher,
        "x265_hf_correlation_higher_on_both_clips": x265_hf_corr_higher,
        "x265_ssim_higher_on_both_clips": all(
            clips[clip]["ssim_all"]["delta_x265_minus_x264"] > 0 for clip in CLIPS
        ),
        "x265_slower_on_both_clips": all(
            clips[clip]["encode_time_s"]["x265_over_x264"] > 1 for clip in CLIPS
        ),
    }


def write_csv_tables(cells: list[dict[str, object]]) -> None:
    summary_fields = [
        "encoder", "clip", "desired_kbps", "target_kbps", "achieved_kbps",
        "relative_error", "converged", "elapsed_s", "sha256", "decoded_md5",
        "cpu_s", "timing_cv", "cpu_timing_cv",
        "ssim_y", "ssim_all", "ssim_min", "ssim_std", "hf_energy_ratio",
        "hf_gain_db", "hf_correlation", "relative_hf_error",
        "buffer_violations", "buffer_min_bits", "allocation_cv",
        "allocation_p95_over_mean", "allocation_max_over_mean",
    ]
    with (RESULTS / "summary.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=summary_fields)
        writer.writeheader()
        for cell in cells:
            writer.writerow(
                {
                    "encoder": cell["encoder"],
                    "clip": cell["clip"],
                    "desired_kbps": cell["desired_kbps"],
                    "target_kbps": cell["target_kbps"],
                    "achieved_kbps": f"{cell['achieved_kbps']:.6f}",
                    "relative_error": f"{cell['relative_error']:.8f}",
                    "converged": cell["converged"],
                    "elapsed_s": f"{cell['elapsed_s']:.6f}",
                    "cpu_s": f"{cell['cpu_s']:.6f}",
                    "timing_cv": f"{cell['timing']['cv']:.8f}",
                    "cpu_timing_cv": f"{cell['timing']['cpu_cv']:.8f}",
                    "sha256": cell["sha256"],
                    "decoded_md5": cell["decoded_md5"],
                    "ssim_y": f"{cell['ssim']['ssim_y']:.8f}",
                    "ssim_all": f"{cell['ssim']['ssim_all']:.8f}",
                    "ssim_min": f"{cell['ssim']['ssim_min']:.8f}",
                    "ssim_std": f"{cell['ssim']['ssim_std']:.8f}",
                    "hf_energy_ratio": f"{cell['high_frequency']['hf_energy_ratio']:.8f}",
                    "hf_gain_db": f"{cell['high_frequency']['hf_gain_db']:.6f}",
                    "hf_correlation": f"{cell['high_frequency']['hf_correlation']:.8f}",
                    "relative_hf_error": f"{cell['high_frequency']['relative_hf_error']:.8f}",
                    "buffer_violations": cell["buffer"]["violations"],
                    "buffer_min_bits": f"{cell['buffer']['min_buffer_bits']:.3f}",
                    "allocation_cv": f"{cell['allocation']['coefficient_of_variation']:.8f}",
                    "allocation_p95_over_mean": f"{cell['allocation']['p95_over_mean']:.8f}",
                    "allocation_max_over_mean": f"{cell['allocation']['max_over_mean']:.8f}",
                }
            )

    fields = [
        "encoder", "clip", "desired_kbps", "achieved_kbps", "frame", "frame_type",
        "frame_bits", "frame_kbps", "normalized_bits", "ssim_y", "ssim_all",
        "hf_energy_ratio", "hf_gain_db", "hf_correlation", "relative_hf_error",
        "buffer_before_bits", "buffer_after_bits", "underflow_bits",
    ]
    with (RESULTS / "per_frame.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for cell in cells:
            for index in range(FRAMES):
                ssim = cell["ssim_frames"][index]
                hf = cell["hf_frames"][index]
                alloc = cell["allocation_frames"][index]
                buffer = cell["buffer_frames"][index]
                writer.writerow(
                    {
                        "encoder": cell["encoder"],
                        "clip": cell["clip"],
                        "desired_kbps": cell["desired_kbps"],
                        "achieved_kbps": f"{cell['achieved_kbps']:.6f}",
                        "frame": index,
                        "frame_type": alloc["type"],
                        "frame_bits": int(alloc["bits"]),
                        "frame_kbps": f"{alloc['kbps']:.6f}",
                        "normalized_bits": f"{alloc['normalized_bits']:.8f}",
                        "ssim_y": f"{ssim['Y']:.8f}",
                        "ssim_all": f"{ssim['All']:.8f}",
                        "hf_energy_ratio": f"{hf['hf_energy_ratio']:.8f}",
                        "hf_gain_db": f"{hf['hf_gain_db']:.6f}",
                        "hf_correlation": f"{hf['hf_correlation']:.8f}",
                        "relative_hf_error": f"{hf['relative_hf_error']:.8f}",
                        "buffer_before_bits": f"{buffer['buffer_before_bits']:.3f}",
                        "buffer_after_bits": f"{buffer['buffer_after_bits']:.3f}",
                        "underflow_bits": f"{buffer['underflow_bits']:.3f}",
                    }
                )


def make_plots(
    cells: list[dict[str, object]], comparisons: dict[str, object]
) -> list[str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    colors = {"x264": "#1f77b4", "x265": "#d62728"}
    markers = {"static": "o", "motion": "s"}
    linestyles = {"static": "-", "motion": "--"}
    primary = primary_map(cells)
    paths: list[str] = []

    fig, axis = plt.subplots(figsize=(8.5, 5.2))
    for encoder in ENCODERS:
        for clip in CLIPS:
            selected = sorted(
                (
                    cell
                    for cell in cells
                    if cell["encoder"] == encoder and cell["clip"] == clip
                ),
                key=lambda item: float(item["achieved_kbps"]),
            )
            axis.plot(
                [cell["achieved_kbps"] for cell in selected],
                [cell["ssim"]["ssim_all"] for cell in selected],
                color=colors[encoder],
                linestyle=linestyles[clip],
                marker=markers[clip],
                label=f"{encoder} / {clip}",
            )
    axis.set_xlabel("Achieved bitrate (kbps)")
    axis.set_ylabel("Mean SSIM (All)")
    axis.set_title("Rate-distortion at matching achieved bitrates")
    axis.grid(alpha=0.25)
    axis.legend()
    fig.tight_layout()
    path = FIGURES / "rd_curve.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    paths.append(str(path.relative_to(ROOT)))

    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8))
    for encoder in ENCODERS:
        for clip in CLIPS:
            selected = sorted(
                (
                    cell
                    for cell in cells
                    if cell["encoder"] == encoder and cell["clip"] == clip
                ),
                key=lambda item: float(item["achieved_kbps"]),
            )
            x = [cell["achieved_kbps"] for cell in selected]
            axes[0].plot(
                x,
                [cell["ssim"]["ssim_all"] for cell in selected],
                color=colors[encoder],
                linestyle=linestyles[clip],
                marker=markers[clip],
                label=f"{encoder} / {clip}",
            )
            axes[1].plot(
                x,
                [cell["elapsed_s"] for cell in selected],
                color=colors[encoder],
                linestyle=linestyles[clip],
                marker=markers[clip],
                label=f"{encoder} / {clip}",
            )
    axes[0].set_title("SSIM curve")
    axes[0].set_xlabel("Achieved bitrate (kbps)")
    axes[0].set_ylabel("Mean SSIM (All)")
    axes[1].set_title("Encoding-time curve")
    axes[1].set_xlabel("Achieved bitrate (kbps)")
    axes[1].set_ylabel("Median wall time, two passes (s)")
    for axis in axes:
        axis.grid(alpha=0.25)
        axis.legend(fontsize=8)
    fig.tight_layout()
    path = FIGURES / "ssim_time_curves.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    paths.append(str(path.relative_to(ROOT)))

    fig, axis = plt.subplots(figsize=(9, 5))
    for encoder in ENCODERS:
        for clip in CLIPS:
            cell = primary[(encoder, clip)]
            rows = cell["ssim_frames"]
            axis.plot(
                [row["frame"] for row in rows],
                [row["All"] for row in rows],
                color=colors[encoder],
                linestyle=linestyles[clip],
                label=f"{encoder} / {clip}",
            )
    axis.set_xlabel("Frame")
    axis.set_ylabel("SSIM (All)")
    axis.set_title(f"Per-frame SSIM at the primary point (~{PRIMARY_KBPS} kbps)")
    axis.grid(alpha=0.25)
    axis.legend()
    fig.tight_layout()
    path = FIGURES / "primary_frame_ssim.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    paths.append(str(path.relative_to(ROOT)))

    fig, axis = plt.subplots(figsize=(9, 5))
    for encoder in ENCODERS:
        for clip in CLIPS:
            cell = primary[(encoder, clip)]
            rows = cell["buffer_frames"]
            axis.plot(
                [row["frame"] for row in rows],
                [row["buffer_after_bits"] / 1000 for row in rows],
                color=colors[encoder],
                linestyle=linestyles[clip],
                label=f"{encoder} / {clip}",
            )
    axis.set_xlabel("Frame")
    axis.set_ylabel("Buffer after frame (kbit)")
    axis.set_title("500 ms leaky-bucket buffer level, initial fill = 100%")
    axis.grid(alpha=0.25)
    axis.legend()
    fig.tight_layout()
    path = FIGURES / "primary_buffer_level.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    paths.append(str(path.relative_to(ROOT)))

    fig, axis = plt.subplots(figsize=(9, 5))
    for encoder in ENCODERS:
        for clip in CLIPS:
            cell = primary[(encoder, clip)]
            bits = np.asarray([row["bits"] for row in cell["allocation_frames"]])
            cumulative = np.cumsum(bits) / bits.sum() * 100
            axis.plot(
                np.arange(FRAMES),
                cumulative,
                color=colors[encoder],
                linestyle=linestyles[clip],
                label=f"{encoder} / {clip}",
            )
    axis.set_xlabel("Frame")
    axis.set_ylabel("Cumulative allocated bits (%)")
    axis.set_title("Within-sequence bitrate allocation at matching bitrate")
    axis.grid(alpha=0.25)
    axis.legend()
    fig.tight_layout()
    path = FIGURES / "primary_bit_allocation.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    paths.append(str(path.relative_to(ROOT)))

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.8))
    positions = np.arange(len(CLIPS))
    width = 0.35
    for offset, encoder in enumerate(ENCODERS):
        values_energy = [
            primary[(encoder, clip)]["high_frequency"]["hf_energy_ratio"]
            for clip in CLIPS
        ]
        values_corr = [
            primary[(encoder, clip)]["high_frequency"]["hf_correlation"]
            for clip in CLIPS
        ]
        axes[0].bar(
            positions + (offset - 0.5) * width,
            values_energy,
            width,
            label=encoder,
            color=colors[encoder],
        )
        axes[1].bar(
            positions + (offset - 0.5) * width,
            values_corr,
            width,
            label=encoder,
            color=colors[encoder],
        )
    for axis, title, ylabel in (
        (axes[0], "High-frequency energy retention", "Decoded / source HF energy"),
        (axes[1], "High-frequency structural correlation", "Correlation of HF planes"),
    ):
        axis.set_xticks(positions)
        axis.set_xticklabels(CLIPS)
        axis.set_title(title)
        axis.set_ylabel(ylabel)
        axis.grid(axis="y", alpha=0.25)
        axis.legend()
    fig.tight_layout()
    path = FIGURES / "hf_detail.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    paths.append(str(path.relative_to(ROOT)))
    return paths


def environment_info() -> dict[str, object]:
    version = run([str(FFMPEG), "-version"], capture_stdout=True).stdout.decode(
        "utf-8", "replace"
    )
    lines = version.splitlines()
    git_commit = run(
        ["git", "rev-parse", "HEAD"], capture_stdout=True, check=False
    ).stdout.decode("ascii", "replace").strip()
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "numpy": np.__version__,
        "cpu_count": platform.os.cpu_count(),
        "git_commit": git_commit,
        "ffmpeg_version": lines[0] if lines else "",
        "ffmpeg_configuration": next(
            (line for line in lines if line.startswith("configuration:")), ""
        ),
        "ffmpeg_sha256": sha256_file(FFMPEG),
        "sources": {
            clip: {
                "raw": str((MEDIA / f"{clip}_640x360_30.yuv").relative_to(ROOT)),
                "raw_sha256": sha256_file(MEDIA / f"{clip}_640x360_30.yuv"),
                "ffv1": str((MEDIA / f"{clip}.mkv").relative_to(ROOT)),
                "ffv1_sha256": sha256_file(MEDIA / f"{clip}.mkv"),
            }
            for clip in CLIPS
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--primary-only",
        action="store_true",
        help="Run only the 252 kbps operating point.",
    )
    args = parser.parse_args()
    ensure_dirs()
    required_sources = [
        MEDIA / "static.mkv",
        MEDIA / "motion.mkv",
        MEDIA / "static_640x360_30.yuv",
        MEDIA / "motion_640x360_30.yuv",
    ]
    if not all(path.exists() for path in required_sources):
        raise SystemExit("Sources are missing; run src/generate_sources.py first.")
    rates = (PRIMARY_KBPS,) if args.primary_only else SWEEP_KBPS
    cells: list[dict[str, object]] = []
    for desired_kbps in rates:
        for encoder in ENCODERS:
            for clip in CLIPS:
                cells.append(collect_cell(encoder, clip, desired_kbps))

    comparisons = build_comparisons(cells)
    reproducibility = build_reproducibility(cells)
    write_json(
        RESULTS / "intermediate.json",
        {
            "cells": cells,
            "comparisons": comparisons,
            "reproducibility": reproducibility,
        },
    )
    plots = make_plots(cells, comparisons)
    environment = environment_info()
    payload = {
        "config": {
            "codecs": list(ENCODERS),
            "clips": list(CLIPS),
            "primary_kbps": PRIMARY_KBPS,
            "sweep_kbps": list(rates),
            "repeats": REPEATS,
            "timing_repeats": TIMING_REPEATS,
            "preset": "medium",
            "threads_per_encode": 1,
            "two_pass": True,
            "gop_frames": 60,
            "buffer_seconds": 0.5,
            "buffer_initial_fill": 1.0,
        },
        "environment": environment,
        "cells": cells,
        "comparisons": comparisons,
        "reproducibility": reproducibility,
        "figures": plots,
    }
    write_json(RESULTS / "results.json", payload)
    write_csv_tables(cells)
    write_json(RESULTS / "comparisons.json", comparisons)
    write_json(RESULTS / "reproducibility.json", reproducibility)
    print(json.dumps(comparisons, indent=2))


if __name__ == "__main__":
    main()
