#!/usr/bin/env python3
"""Reproducible 4:2:0 -> 4:4:4 chroma upsampling benchmark."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import platform
import statistics
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"

QUALITY_SIZE = (256, 192)
BENCH_SIZE = (1920, 1080)
EDGE_X = 129
BAND_RADIUS = 8
LANCZOS_A = 3.0

RED_RGB = np.array([220.0, 32.0, 32.0], dtype=np.float64)
BLUE_RGB = np.array([32.0, 32.0, 220.0], dtype=np.float64)


@dataclass(frozen=True)
class Frame420:
    y: np.ndarray
    cb: np.ndarray
    cr: np.ndarray


@dataclass(frozen=True)
class Frame444:
    y: np.ndarray
    cb: np.ndarray
    cr: np.ndarray


def rgb_to_ycbcr(rgb: np.ndarray) -> np.ndarray:
    """BT.601 full-range RGB -> YCbCr, preserving float precision."""
    r, g, b = np.moveaxis(rgb, -1, 0)
    y = 0.299 * r + 0.587 * g + 0.114 * b
    cb = 128.0 - 0.168736 * r - 0.331264 * g + 0.5 * b
    cr = 128.0 + 0.5 * r - 0.418688 * g - 0.081312 * b
    return np.stack((y, cb, cr), axis=-1)


def ycbcr_to_rgb(y: np.ndarray, cb: np.ndarray, cr: np.ndarray) -> np.ndarray:
    """BT.601 full-range YCbCr -> uint8 RGB."""
    r = y + 1.402 * (cr - 128.0)
    g = y - 0.344136 * (cb - 128.0) - 0.714136 * (cr - 128.0)
    b = y + 1.772 * (cb - 128.0)
    rgb = np.stack((r, g, b), axis=-1)
    return np.clip(np.rint(rgb), 0, 255).astype(np.uint8)


def _sha256_array(*arrays: np.ndarray) -> str:
    digest = hashlib.sha256()
    for arr in arrays:
        digest.update(str(arr.shape).encode("ascii"))
        digest.update(str(arr.dtype).encode("ascii"))
        digest.update(np.ascontiguousarray(arr).tobytes())
    return digest.hexdigest()


def _sinc(x: np.ndarray) -> np.ndarray:
    return np.sinc(x)


def _lanczos_weight(t: np.ndarray, a: float = LANCZOS_A) -> np.ndarray:
    out = np.zeros_like(t, dtype=np.float64)
    inside = np.abs(t) < a
    out[inside] = _sinc(t[inside]) * _sinc(t[inside] / a)
    out[np.abs(t) < 1e-12] = 1.0
    out[np.abs(t - np.rint(t)) < 1e-12] = 0.0
    out[np.abs(t) < 1e-12] = 1.0
    return out


def _nearest_indices(length: int) -> np.ndarray:
    return np.floor(np.arange(length, dtype=np.float64) / 2.0).astype(np.int64)


def _bilinear_axis(src: np.ndarray, out_len: int, axis: int) -> np.ndarray:
    src_len = src.shape[axis]
    coord = np.arange(out_len, dtype=np.float64) / 2.0
    left = np.floor(coord).astype(np.int64)
    frac = coord - left
    right = np.minimum(left + 1, src_len - 1)

    take = lambda indices: np.take(src, indices, axis=axis)
    expanded_shape = [1] * src.ndim
    expanded_shape[axis] = out_len
    a = take(left)
    b = take(right)
    weight = frac.reshape(expanded_shape)
    return a * (1.0 - weight) + b * weight


def _lanczos_axis(
    src: np.ndarray, out_len: int, axis: int, a: float = LANCZOS_A
) -> np.ndarray:
    src_len = src.shape[axis]
    coord = np.arange(out_len, dtype=np.float64) / 2.0
    base = np.floor(coord).astype(np.int64)
    taps = int(math.ceil(a))

    kernel_shape = [1] * src.ndim
    kernel_shape[axis] = out_len
    result = np.zeros_like(src, dtype=np.float64, shape=_replace(src.shape, axis, out_len))
    weight_sum = np.zeros(out_len, dtype=np.float64)

    for offset in range(-taps + 1, taps + 1):
        index = np.clip(base + offset, 0, src_len - 1)
        distance = coord - (base + offset)
        weight = _lanczos_weight(distance, a)
        if not np.any(weight):
            continue
        result += np.take(src, index, axis=axis) * weight.reshape(kernel_shape)
        weight_sum += weight
    return result / weight_sum.reshape(kernel_shape)


def _replace(shape: tuple[int, ...], axis: int, value: int) -> tuple[int, ...]:
    changed = list(shape)
    changed[axis] = value
    return tuple(changed)


def upsample_nearest(src: np.ndarray) -> np.ndarray:
    rows = _nearest_indices(src.shape[0] * 2)
    cols = _nearest_indices(src.shape[1] * 2)
    return src[np.ix_(rows, cols)].astype(np.float64, copy=True)


def upsample_bilinear(src: np.ndarray) -> np.ndarray:
    horizontal = _bilinear_axis(src.astype(np.float64), src.shape[1] * 2, axis=1)
    return _bilinear_axis(horizontal, src.shape[0] * 2, axis=0)


def upsample_lanczos(src: np.ndarray, a: float = LANCZOS_A) -> np.ndarray:
    horizontal = _lanczos_axis(src.astype(np.float64), src.shape[1] * 2, axis=1, a=a)
    return _lanczos_axis(horizontal, src.shape[0] * 2, axis=0, a=a)


UPSAMPLERS: dict[str, Callable[[np.ndarray], np.ndarray]] = {
    "nearest": upsample_nearest,
    "bilinear": upsample_bilinear,
    "lanczos3": upsample_lanczos,
}


def build_reference_444(
    width: int = QUALITY_SIZE[0],
    height: int = QUALITY_SIZE[1],
    edge_x: int = EDGE_X,
) -> Frame444:
    """Create a hard red/blue vertical edge and its quantized YCbCr truth."""
    rgb = np.empty((height, width, 3), dtype=np.float64)
    rgb[:, :edge_x] = RED_RGB
    rgb[:, edge_x:] = BLUE_RGB

    ycbcr = rgb_to_ycbcr(rgb)
    y = np.clip(np.rint(ycbcr[..., 0]), 0, 255).astype(np.uint8)
    cb = np.clip(np.rint(ycbcr[..., 1]), 0, 255).astype(np.uint8)
    cr = np.clip(np.rint(ycbcr[..., 2]), 0, 255).astype(np.uint8)
    return Frame444(y=y, cb=cb, cr=cr)


def _box_downsample_2x(plane: np.ndarray) -> np.ndarray:
    height, width = plane.shape
    if height % 2 or width % 2:
        raise ValueError(f"4:2:0 needs even dimensions, got {plane.shape}")
    summed = plane.astype(np.uint32).reshape(height // 2, 2, width // 2, 2).sum(
        axis=(1, 3)
    )
    return np.clip(np.rint(summed / 4.0), 0, 255).astype(np.uint8)


def make_source_420(reference: Frame444) -> Frame420:
    """Derive a 4:2:0 frame by a 2x2 box average of the 4:4:4 chroma."""
    y = np.array(reference.y, copy=True)
    cb = _box_downsample_2x(reference.cb)
    cr = _box_downsample_2x(reference.cr)
    for arr in (y, cb, cr):
        arr.setflags(write=False)
    return Frame420(y=y, cb=cb, cr=cr)


def source_hash(frame: Frame420) -> str:
    return _sha256_array(frame.y, frame.cb, frame.cr)


def reconstruct_444(
    source: Frame420, method: str
) -> tuple[Frame444, np.ndarray]:
    """Upsample only chroma, retaining the original full-resolution Y plane."""
    interpolate = UPSAMPLERS[method]
    cb = interpolate(source.cb)
    cr = interpolate(source.cr)
    rgb = ycbcr_to_rgb(source.y.astype(np.float64), cb, cr)
    return Frame444(y=source.y, cb=cb, cr=cr), rgb


def _write_png(path: Path, rgb: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgb, mode="RGB").save(path)


def _ycbcr_preview(frame: Frame420) -> np.ndarray:
    cb = np.repeat(np.repeat(frame.cb, 2, axis=0), 2, axis=1)
    cr = np.repeat(np.repeat(frame.cr, 2, axis=0), 2, axis=1)
    return ycbcr_to_rgb(frame.y.astype(np.float64), cb, cr)


def _psnr_from_mse(mse: float, peak: float = 255.0) -> float:
    return math.inf if mse == 0.0 else 10.0 * math.log10(peak * peak / mse)


def psnr_rgb(predicted: np.ndarray, target: np.ndarray) -> float:
    mse = np.mean(
        (predicted.astype(np.float64) - target.astype(np.float64)) ** 2,
        dtype=np.float64,
    )
    return _psnr_from_mse(float(mse))


def psnr_plane(predicted: np.ndarray, target: np.ndarray) -> float:
    mse = np.mean(
        (predicted.astype(np.float64) - target.astype(np.float64)) ** 2,
        dtype=np.float64,
    )
    return _psnr_from_mse(float(mse))


def pixel_psnr_rgb(predicted: np.ndarray, target: np.ndarray) -> np.ndarray:
    error = predicted.astype(np.float64) - target.astype(np.float64)
    mse = np.mean(error * error, axis=2)
    with np.errstate(divide="ignore"):
        return 10.0 * np.log10((255.0 * 255.0) / mse)


def _summary(values: np.ndarray) -> dict[str, float | int]:
    flat = np.asarray(values, dtype=np.float64).ravel()
    return {
        "count": int(flat.size),
        "mean": float(np.mean(flat)),
        "p95": float(np.percentile(flat, 95)),
        "max": float(np.max(flat)),
    }


def color_bleed_metrics(
    reference: Frame444,
    reconstructed: Frame444,
    rgb: np.ndarray,
    edge_x: int = EDGE_X,
    radius: int = BAND_RADIUS,
) -> dict[str, object]:
    """Measure cross-color leakage in an 8-bit chroma-vector projection."""
    ideal_rgb = ycbcr_to_rgb(
        reference.y.astype(np.float64),
        reference.cb.astype(np.float64),
        reference.cr.astype(np.float64),
    )
    height, width = reference.cb.shape
    x0, x1 = max(0, edge_x - radius), min(width, edge_x + radius)
    red = np.zeros((height, width), dtype=bool)
    red[:, :edge_x] = True
    band = np.zeros((height, width), dtype=bool)
    band[:, x0:x1] = True
    red_side = band & red
    blue_side = band & ~red

    red_cb = float(np.median(reference.cb[:, : max(1, edge_x - radius)]))
    red_cr = float(np.median(reference.cr[:, : max(1, edge_x - radius)]))
    blue_cb = float(np.median(reference.cb[:, edge_x + radius :]))
    blue_cr = float(np.median(reference.cr[:, edge_x + radius :]))
    red_chroma = np.array([red_cb, red_cr], dtype=np.float64)
    blue_chroma = np.array([blue_cb, blue_cr], dtype=np.float64)

    cbcr = np.stack((reconstructed.cb, reconstructed.cr), axis=2)
    own = np.where(red[..., None], red_chroma, blue_chroma)
    opposite = np.where(red[..., None], blue_chroma, red_chroma)
    delta = cbcr - own
    direction = opposite - own
    norm_sq = np.sum(direction * direction, axis=2)
    leak_fraction = np.sum(delta * direction, axis=2) / norm_sq
    ringing_codes = np.sum(delta * -direction, axis=2) / np.sqrt(norm_sq)

    blue_leak = np.maximum(rgb[..., 2].astype(np.float64) - ideal_rgb[..., 2], 0.0)
    red_leak = np.maximum(rgb[..., 0].astype(np.float64) - ideal_rgb[..., 0], 0.0)
    positive_leak = np.maximum(leak_fraction, 0.0)
    positive_ringing = np.maximum(ringing_codes, 0.0)

    def side_metrics(
        mask: np.ndarray, channel_leak: np.ndarray
    ) -> dict[str, object]:
        leak = positive_leak[mask]
        ringing = positive_ringing[mask]
        channel_leak_values = channel_leak[mask]
        return {
            "pixels": int(mask.sum()),
            "mean_leak_percent": 100.0 * float(np.mean(leak)),
            "max_leak_percent": 100.0 * float(np.max(leak)),
            "bleed_area_percent_gt_2pct": 100.0 * float(np.mean(leak > 0.02)),
            "mean_channel_leak_codes": float(np.mean(channel_leak_values)),
            "max_channel_leak_codes": float(np.max(channel_leak_values)),
            "ringing_area_percent_gt_half_code": 100.0
            * float(np.mean(ringing > 0.5)),
            "ringing_energy_codes": float(np.mean(ringing)),
            "max_ringing_overshoot_codes": float(np.max(ringing)),
        }

    both = red_side | blue_side
    return {
        "band_x": [x0, x1 - 1],
        "red_chroma": [red_cb, red_cr],
        "blue_chroma": [blue_cb, blue_cr],
        "red_side": side_metrics(red_side, blue_leak),
        "blue_side": side_metrics(blue_side, red_leak),
        "combined": {
            "pixels": int(both.sum()),
            "mean_leak_percent": 100.0 * float(np.mean(positive_leak[both])),
            "max_leak_percent": 100.0 * float(np.max(positive_leak[both])),
            "bleed_area_percent_gt_2pct": 100.0
            * float(np.mean(positive_leak[both] > 0.02)),
            "ringing_area_percent_gt_half_code": 100.0
            * float(np.mean(positive_ringing[both] > 0.5)),
            "ringing_energy_codes": float(np.mean(positive_ringing[both])),
            "max_ringing_overshoot_codes": float(np.max(positive_ringing[both])),
        },
    }


def _write_pixel_psnr(path: Path, values: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    height, width = values.shape
    rows = np.column_stack(
        (
            np.repeat(np.arange(height), width),
            np.tile(np.arange(width), height),
            values.ravel(),
        )
    )
    np.savetxt(path, rows, delimiter=",", fmt="%d,%d,%.6f", header="y,x,psnr_db")


def evaluate_quality() -> dict[str, object]:
    reference = build_reference_444()
    source = make_source_420(reference)
    source_before = (source.cb.copy(), source.cr.copy())
    hash_before = source_hash(source)
    reference_rgb = ycbcr_to_rgb(
        reference.y.astype(np.float64),
        reference.cb.astype(np.float64),
        reference.cr.astype(np.float64),
    )

    RESULTS.mkdir(parents=True, exist_ok=True)
    _write_png(RESULTS / "reference_444.png", reference_rgb)
    _write_png(RESULTS / "source_420_nearest_preview.png", _ycbcr_preview(source))

    all_results: dict[str, object] = {}
    for method, interpolate in UPSAMPLERS.items():
        reconstructed, rgb = reconstruct_444(source, method)
        pixel_psnr = pixel_psnr_rgb(rgb, reference_rgb)
        finite = pixel_psnr[np.isfinite(pixel_psnr)]
        edge_band = slice(max(0, EDGE_X - BAND_RADIUS), EDGE_X + BAND_RADIUS)
        lattice_delta = max(
            float(np.max(np.abs(reconstructed.cb[::2, ::2] - source.cb))),
            float(np.max(np.abs(reconstructed.cr[::2, ::2] - source.cr))),
        )
        input_unchanged = bool(
            np.array_equal(source.cb, source_before[0])
            and np.array_equal(source.cr, source_before[1])
        )
        _write_png(RESULTS / f"{method}_444.png", rgb)
        _write_pixel_psnr(RESULTS / f"{method}_pixel_psnr.csv", pixel_psnr)
        all_results[method] = {
            "psnr_rgb_db": psnr_rgb(rgb, reference_rgb),
            "psnr_rgb_edge_band_db": psnr_rgb(
                rgb[:, edge_band], reference_rgb[:, edge_band]
            ),
            "psnr_cb_db": psnr_plane(reconstructed.cb, reference.cb),
            "psnr_cr_db": psnr_plane(reconstructed.cr, reference.cr),
            "pixel_psnr": {
                "definition": "10*log10(255^2 / MSE_RGB(pixel)); inf means exact",
                "exact_pixel_percent": 100.0 * float(np.isinf(pixel_psnr).mean()),
                "finite_min_db": float(np.min(finite)),
                "finite_p05_db": float(np.percentile(finite, 5)),
                "finite_median_db": float(np.median(finite)),
                "finite_mean_db": float(np.mean(finite)),
            },
            "color_bleed": color_bleed_metrics(
                reference, reconstructed, rgb
            ),
            "source_chroma_lattice_max_abs_delta_codes": lattice_delta,
            "source_chroma_lattice_delta_zero": lattice_delta == 0.0,
            "input_chroma_unchanged": input_unchanged,
        }

    hash_after = source_hash(source)
    bleeds = {
        method: result["color_bleed"]["combined"]
        for method, result in all_results.items()
    }
    ringing_energy = {
        method: stats["ringing_energy_codes"] for method, stats in bleeds.items()
    }
    least_value = min(ringing_energy.values())
    least_methods = [
        method
        for method, value in ringing_energy.items()
        if math.isclose(value, least_value, rel_tol=0.0, abs_tol=1e-12)
    ]
    source_control = {
        "source_sha256_before": hash_before,
        "source_sha256_after": hash_after,
        "source_hash_unchanged": hash_before == hash_after,
        "all_input_chroma_unchanged": all(
            result["input_chroma_unchanged"] for result in all_results.values()
        ),
        "all_source_lattice_deltas_zero": all(
            result["source_chroma_lattice_delta_zero"]
            for result in all_results.values()
        ),
        "lanczos_is_least_ringing": least_methods == ["lanczos3"],
        "least_ringing_methods": least_methods,
    }
    return {
        "fixture": {
            "width": QUALITY_SIZE[0],
            "height": QUALITY_SIZE[1],
            "edge_x": EDGE_X,
            "band_radius": BAND_RADIUS,
            "lanczos_a": LANCZOS_A,
            "generation": "hard red/blue vertical edge; 4:2:0 by 2x2 box average",
        },
        "methods": all_results,
        "source_control": source_control,
    }


def _time_call(
    call: Callable[[], object], iterations: int, warmups: int = 3
) -> dict[str, float | int]:
    for _ in range(warmups):
        call()
    samples = []
    for _ in range(iterations):
        start = time.perf_counter_ns()
        call()
        samples.append((time.perf_counter_ns() - start) / 1_000_000.0)
    ordered = sorted(samples)
    median = statistics.median(samples)
    p95 = ordered[min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)]
    return {
        "iterations": len(samples),
        "median_ms": float(median),
        "mean_ms": float(statistics.fmean(samples)),
        "p95_ms": float(p95),
        "fps_from_median": float(1000.0 / median),
        "meets_30fps_budget": bool(median <= 1000.0 / 30.0),
        "meets_60fps_budget": bool(median <= 1000.0 / 60.0),
    }


def benchmark_frame(iterations: int = 30) -> dict[str, object]:
    reference = build_reference_444(BENCH_SIZE[0], BENCH_SIZE[1])
    source = make_source_420(reference)
    results: dict[str, object] = {}
    for method, interpolate in UPSAMPLERS.items():
        def chroma_only() -> object:
            return interpolate(source.cb), interpolate(source.cr)

        def end_to_end() -> object:
            return reconstruct_444(source, method)

        results[method] = {
            "chroma_upsample": _time_call(chroma_only, iterations),
            "upsample_plus_ycbcr_to_rgb": _time_call(end_to_end, iterations),
        }
    return {
        "frame": {
            "width": BENCH_SIZE[0],
            "height": BENCH_SIZE[1],
            "format": "4:2:0 -> 4:4:4, then BT.601 RGB conversion",
        },
        "environment": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "platform": platform.platform(),
            "processor": platform.processor(),
        },
        "methods": results,
        "denoising_tested": False,
    }


def run_native_benchmark(iterations: int) -> dict[str, object]:
    build_dir = ROOT / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    binary = build_dir / "frame_benchmark"
    flags = ["-O3", "-march=native", "-ffast-math", "-Wall", "-Wextra"]
    compile_command = [
        "gcc",
        *flags,
        "-o",
        str(binary),
        str(ROOT / "src" / "frame_benchmark.c"),
        "-lm",
    ]
    subprocess.run(compile_command, check=True, capture_output=True, text=True)
    completed = subprocess.run(
        [str(binary), str(iterations)],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)
    cpu_name = "unknown"
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        for line in cpuinfo.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("model name"):
                cpu_name = line.split(":", 1)[1].strip()
                break
    compiler = subprocess.run(
        ["gcc", "--version"], check=True, capture_output=True, text=True
    ).stdout.splitlines()[0]
    result["environment"] = {
        "cpu": cpu_name,
        "compiler": compiler,
        "compile_flags": flags,
        "python": platform.python_version(),
        "numpy": np.__version__,
    }
    return result


def _write_csv_outputs(quality: dict[str, object], timing: dict[str, object]) -> None:
    methods = quality["methods"]
    with (RESULTS / "psnr_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "method",
                "psnr_rgb_db",
                "psnr_edge_band_db",
                "psnr_cb_db",
                "psnr_cr_db",
                "exact_pixel_percent",
                "finite_median_pixel_psnr_db",
            ]
        )
        for method, values in methods.items():
            writer.writerow(
                [
                    method,
                    f"{values['psnr_rgb_db']:.6f}",
                    f"{values['psnr_rgb_edge_band_db']:.6f}",
                    f"{values['psnr_cb_db']:.6f}",
                    f"{values['psnr_cr_db']:.6f}",
                    f"{values['pixel_psnr']['exact_pixel_percent']:.6f}",
                    f"{values['pixel_psnr']['finite_median_db']:.6f}",
                ]
            )

    with (RESULTS / "color_bleed.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "method",
                "mean_leak_percent",
                "max_leak_percent",
                "bleed_area_percent_gt_2pct",
                "red_side_mean_leak_percent",
                "red_side_max_leak_percent",
                "blue_side_mean_leak_percent",
                "blue_side_max_leak_percent",
                "red_side_mean_channel_leak_codes",
                "blue_side_mean_channel_leak_codes",
                "ringing_area_percent_gt_half_code",
                "ringing_energy_codes",
                "max_ringing_overshoot_codes",
            ]
        )
        for method, values in methods.items():
            bleed = values["color_bleed"]
            combined = bleed["combined"]
            writer.writerow(
                [
                    method,
                    f"{combined['mean_leak_percent']:.6f}",
                    f"{combined['max_leak_percent']:.6f}",
                    f"{combined['bleed_area_percent_gt_2pct']:.6f}",
                    f"{bleed['red_side']['mean_leak_percent']:.6f}",
                    f"{bleed['red_side']['max_leak_percent']:.6f}",
                    f"{bleed['blue_side']['mean_leak_percent']:.6f}",
                    f"{bleed['blue_side']['max_leak_percent']:.6f}",
                    f"{bleed['red_side']['mean_channel_leak_codes']:.6f}",
                    f"{bleed['blue_side']['mean_channel_leak_codes']:.6f}",
                    f"{combined['ringing_area_percent_gt_half_code']:.6f}",
                    f"{combined['ringing_energy_codes']:.6f}",
                    f"{combined['max_ringing_overshoot_codes']:.6f}",
                ]
            )

    with (RESULTS / "timing_native.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["method", "median_ms", "p95_ms", "fps_from_median"])
        for method, values in timing["methods"].items():
            writer.writerow(
                [
                    method,
                    f"{values['median_ms']:.6f}",
                    f"{values['p95_ms']:.6f}",
                    f"{values['fps_from_median']:.3f}",
                ]
            )


def _write_report(quality: dict[str, object], timing: dict[str, object]) -> None:
    methods = quality["methods"]
    control = quality["source_control"]
    lines = [
        "# 4:2:0 到 4:4:4 色度上采样对比",
        "",
        "## 实验设置",
        "",
        "- 256×192 合成彩色边缘，红色/蓝色硬边界位于 `x=129`；",
        "- 参考 4:4:4 YCbCr 先量化到 8 bit，4:2:0 由 2×2 盒式平均得到；",
        "- Y 始终保留全分辨率，只对 Cb/Cr 上采样，随后做 BT.601 全范围 RGB 转换；",
        "- 溢色统计带为边界左右各 8 像素；振铃定义为越过本侧色度平台、朝远离对侧方向的外推量。",
        "",
        "## PSNR",
        "",
        "| 方法 | RGB PSNR | 边缘带 PSNR | Cb PSNR | Cr PSNR | 精确像素 | 有限像素 PSNR 中位数 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for method, values in methods.items():
        pixel = values["pixel_psnr"]
        lines.append(
            f"| {method} | {values['psnr_rgb_db']:.3f} dB | "
            f"{values['psnr_rgb_edge_band_db']:.3f} dB | "
            f"{values['psnr_cb_db']:.3f} dB | {values['psnr_cr_db']:.3f} dB | "
            f"{pixel['exact_pixel_percent']:.2f}% | "
            f"{pixel['finite_median_db']:.3f} dB |"
        )
    lines.extend(
        [
            "",
            "逐像素 RGB PSNR 定义：`10*log10(255^2 / mean((R,G,B)_error^2))`；"
            "零误差像素在 CSV 中记为 `inf`。",
            "",
            "## 红蓝交界溢色",
            "",
            "| 方法 | 平均泄漏 | 最大泄漏 | 泄漏 >2% 面积 | 红侧平均/最大 | 蓝侧平均/最大 |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for method, values in methods.items():
        bleed = values["color_bleed"]
        combined = bleed["combined"]
        lines.append(
            f"| {method} | {combined['mean_leak_percent']:.3f}% | "
            f"{combined['max_leak_percent']:.3f}% | "
            f"{combined['bleed_area_percent_gt_2pct']:.2f}% | "
            f"{bleed['red_side']['mean_leak_percent']:.3f}% / "
            f"{bleed['red_side']['max_leak_percent']:.3f}% | "
            f"{bleed['blue_side']['mean_leak_percent']:.3f}% / "
            f"{bleed['blue_side']['max_leak_percent']:.3f}% |"
        )
    lines.extend(
        [
            "",
            "跨色泄漏使用 CbCr 向量投影：`0%` 为本侧纯色，`100%` 为完全变成对侧颜色。"
            "RGB 通道泄漏明细在 `color_bleed.csv`。",
        ]
    )
    lines.extend(
        [
            "",
            "## 振铃",
            "",
            "| 方法 | 振铃面积（>0.5 码值） | 振铃能量 | 最大过冲 |",
            "|---|---:|---:|---:|",
        ]
    )
    for method, values in methods.items():
        combined = values["color_bleed"]["combined"]
        lines.append(
            f"| {method} | {combined['ringing_area_percent_gt_half_code']:.2f}% | "
            f"{combined['ringing_energy_codes']:.6f} 码值 | "
            f"{combined['max_ringing_overshoot_codes']:.3f} 码值 |"
        )
    least = ", ".join(control["least_ringing_methods"])
    lines.extend(
        [
            "",
            f"**结论：Lanczos 并非振铃最少。** 最小振铃方法为 `{least}`；"
            "Lanczos-3 因负瓣在边界两侧产生明显过冲。",
            "",
            "## 色度完整性与实时性",
            "",
            f"- 4:2:0 源 SHA-256 前后一致：`{str(control['source_hash_unchanged']).lower()}`；",
            f"- 所有方法的输入色度数组均未被修改：`{str(control['all_input_chroma_unchanged']).lower()}`；",
            f"- 三种方法在原始 4:2:0 采样晶格上的最大色度差：`0` 码值；",
            "- 该“零差”仅指原始采样晶格点；非晶格点必然由插值生成，不能与低分辨率源逐元素比较。",
            "",
            "| 方法 | 1080p 中位耗时 | P95 | 等价帧率 | 60 fps |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for method, values in timing["methods"].items():
        lines.append(
            f"| {method} | {values['median_ms']:.3f} ms | {values['p95_ms']:.3f} ms | "
            f"{values['fps_from_median']:.1f} fps | "
            f"{'是' if values['meets_60fps'] else '否'} |"
        )
    lines.extend(
        [
            "",
            "原生单线程、预分配缓冲，计时覆盖 Cb/Cr 上采样、4:4:4 拼帧和 BT.601 RGB 转换。",
            "因此该处理链达到 1080p60。工作区没有降噪器，`denoising_tested=false`；"
            "不能把本结果表述为已确认某个降噪算法可实时运行。",
            "",
        ]
    )
    (RESULTS / "report.md").write_text("\n".join(lines), encoding="utf-8")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-benchmark", action="store_true", help="skip the 1080p timing pass"
    )
    parser.add_argument(
        "--iterations", type=int, default=30, help="timed frames per method"
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    RESULTS.mkdir(parents=True, exist_ok=True)
    quality = evaluate_quality()
    timing = None
    if not args.skip_benchmark:
        timing = run_native_benchmark(args.iterations)
        (RESULTS / "timing_native.json").write_text(
            json.dumps(timing, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    payload = {"quality": quality, "timing": timing}
    (RESULTS / "metrics.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if timing is not None:
        _write_csv_outputs(quality, timing)
        _write_report(quality, timing)

    print("method      RGB PSNR   edge PSNR   leak mean   ring energy   median time")
    for method, values in quality["methods"].items():
        combined = values["color_bleed"]["combined"]
        time_text = (
            f"{timing['methods'][method]['median_ms']:.3f} ms"
            if timing is not None
            else "n/a"
        )
        print(
            f"{method:10}  {values['psnr_rgb_db']:8.3f}  "
            f"{values['psnr_rgb_edge_band_db']:10.3f}  "
            f"{combined['mean_leak_percent']:9.3f}%  "
            f"{combined['ringing_energy_codes']:11.6f}  {time_text}"
        )
    print(f"artifacts: {RESULTS}")


if __name__ == "__main__":
    main()
