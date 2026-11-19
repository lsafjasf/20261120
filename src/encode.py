#!/usr/bin/env python3
"""Two-pass encoding and achieved-bitrate calibration."""

from __future__ import annotations

import shutil
from pathlib import Path

from common import (
    BITRATE_TOLERANCE,
    DURATION_S,
    ENCODED,
    ENCODERS,
    FFMPEG,
    FFPROBE,
    FPS,
    HEIGHT,
    WIDTH,
    WORK,
    reset_dir,
    run,
    sha256_file,
)


def _common_args(encoder: str, target_kbps: int) -> list[str]:
    args = [
        "-c:v",
        ENCODERS[encoder]["codec"],
        "-preset",
        "medium",
        "-threads",
        "1",
        "-b:v",
        f"{target_kbps}k",
        "-pix_fmt",
        "yuv420p",
        "-an",
    ]
    if encoder == "x264":
        args += ["-x264-params", "keyint=60:min-keyint=60:scenecut=0"]
    return args


def encode_twopass(
    encoder: str,
    source: Path,
    output: Path,
    target_kbps: int,
    work_dir: Path,
) -> tuple[float, float, float]:
    """Encode with two-pass ABR; return (wall_s, cpu_s, achieved_kbps)."""
    reset_dir(work_dir)
    stats = work_dir / "stats"
    output.parent.mkdir(parents=True, exist_ok=True)
    common = _common_args(encoder, target_kbps)
    if source.suffix == ".yuv":
        input_args = [
            "-f", "rawvideo", "-pix_fmt", "yuv420p",
            "-s", f"{WIDTH}x{HEIGHT}", "-r", str(FPS), "-i", str(source),
        ]
    else:
        input_args = ["-i", str(source)]

    if encoder == "x264":
        pass1 = [
            str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
            *input_args, *common,
            "-pass", "1", "-passlogfile", str(stats),
            "-f", "null", "/dev/null",
        ]
        pass2 = [
            str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
            *input_args, *common,
            "-pass", "2", "-passlogfile", str(stats),
            "-f", "h264", str(output),
        ]
    else:
        pass1 = [
            str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
            *input_args, *common,
            "-x265-params",
            "pools=1:frame-threads=1:keyint=60:min-keyint=60:scenecut=0"
            f":log-level=error:pass=1:stats={stats}",
            "-f", "null", "/dev/null",
        ]
        pass2 = [
            str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
            *input_args, *common,
            "-x265-params",
            "pools=1:frame-threads=1:keyint=60:min-keyint=60:scenecut=0"
            f":log-level=error:pass=2:stats={stats}",
            "-f", "hevc", str(output),
        ]
    first = run(pass1)
    second = run(pass2)
    achieved_kbps = output.stat().st_size * 8 / DURATION_S / 1000
    return (
        first.elapsed_s + second.elapsed_s,
        first.cpu_s + second.cpu_s,
        achieved_kbps,
    )


def verify_stream(path: Path) -> dict[str, object]:
    result = run(
        [
            str(FFPROBE), "-v", "error", "-select_streams", "v:0",
            "-count_frames", "-show_entries",
            "stream=codec_name,width,height,pix_fmt,nb_read_frames",
            "-of", "json", str(path),
        ],
        capture_stdout=True,
    )
    import json

    payload = json.loads(result.stdout)
    return payload["streams"][0]


def calibrate_rate(
    encoder: str,
    clip: str,
    source: Path,
    desired_kbps: int,
    *,
    max_iterations: int = 8,
) -> dict[str, object]:
    """Search the ABR target until the achieved bitrate matches desired_kbps."""
    calibration_dir = WORK / "calibration" / f"{encoder}_{clip}_{desired_kbps}"
    calibration_dir.mkdir(parents=True, exist_ok=True)
    guess = float(desired_kbps)
    history: list[dict[str, float]] = []
    best: tuple[float, Path, float, float, int] | None = None

    for iteration in range(1, max_iterations + 1):
        run_dir = calibration_dir / f"iter_{iteration:02d}"
        candidate = run_dir / f"candidate{ENCODERS[encoder]['extension']}"
        elapsed_s, cpu_s, achieved_kbps = encode_twopass(
            encoder, source, candidate, max(10, round(guess)), run_dir
        )
        error = abs(achieved_kbps - desired_kbps) / desired_kbps
        history.append(
            {
                "iteration": iteration,
                "target_kbps": max(10, round(guess)),
                "achieved_kbps": round(achieved_kbps, 3),
                "relative_error": round(error, 6),
                "elapsed_s": round(elapsed_s, 6),
                "cpu_s": round(cpu_s, 6),
            }
        )
        if best is None or error < best[0]:
            best = (error, candidate, elapsed_s, cpu_s, max(10, round(guess)))
        if error <= BITRATE_TOLERANCE:
            break
        guess = min(5000.0, max(10.0, guess * desired_kbps / achieved_kbps))

    assert best is not None
    best_error, best_candidate, best_elapsed_s, best_cpu_s, best_target = best
    output = ENCODED / f"{encoder}_{clip}_{desired_kbps}k{ENCODERS[encoder]['extension']}"
    shutil.copyfile(best_candidate, output)
    stream = verify_stream(output)
    return {
        "encoder": encoder,
        "clip": clip,
        "desired_kbps": desired_kbps,
        "target_kbps": best_target,
        "achieved_kbps": output.stat().st_size * 8 / DURATION_S / 1000,
        "relative_error": best_error,
        "converged": best_error <= BITRATE_TOLERANCE,
        "iterations": history,
        "elapsed_s": best_elapsed_s,
        "cpu_s": best_cpu_s,
        "output": str(output.relative_to(output.parents[2])),
        "sha256": sha256_file(output),
        "stream": stream,
    }
