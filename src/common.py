#!/usr/bin/env python3
"""Shared paths, experiment configuration, and subprocess helpers."""

from __future__ import annotations

import hashlib
import json
import os
import resource
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
MEDIA = ARTIFACTS / "media"
ENCODED = ARTIFACTS / "encoded"
WORK = ARTIFACTS / "work"
FIGURES = ARTIFACTS / "figures"
RESULTS = ARTIFACTS / "results"
FFMPEG = ROOT / ".tools" / "ffmpeg-dist" / "ffmpeg"
FFPROBE = ROOT / ".tools" / "ffmpeg-dist" / "ffprobe"

WIDTH = 640
HEIGHT = 360
FPS = 30
FRAMES = 90
DURATION_S = FRAMES / FPS
PRIMARY_KBPS = 252
SWEEP_KBPS = (150, 200, 252, 350, 500)
REPEATS = 3
TIMING_REPEATS = 3
BITRATE_TOLERANCE = 0.01

ENCODERS = {
    "x264": {
        "codec": "libx264",
        "extension": ".h264",
        "pix_fmt": "yuv420p",
    },
    "x265": {
        "codec": "libx265",
        "extension": ".hevc",
        "pix_fmt": "yuv420p",
    },
}


@dataclass
class CommandResult:
    command: list[str]
    elapsed_s: float
    cpu_s: float
    stdout: bytes
    stderr: str
    returncode: int


def ensure_dirs() -> None:
    for path in (MEDIA, ENCODED, WORK, FIGURES, RESULTS):
        path.mkdir(parents=True, exist_ok=True)


def run(
    command: list[str],
    *,
    capture_stdout: bool = False,
    check: bool = True,
    timeout: float | None = None,
) -> CommandResult:
    started = time.perf_counter()
    usage_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    proc = subprocess.run(
        command,
        stdout=subprocess.PIPE if capture_stdout else subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    elapsed = time.perf_counter() - started
    usage_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu_s = (
        usage_after.ru_utime
        + usage_after.ru_stime
        - usage_before.ru_utime
        - usage_before.ru_stime
    )
    if check and proc.returncode:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(command)}\n"
            + proc.stderr.decode("utf-8", "replace")[-4000:]
        )
    return CommandResult(
        command,
        elapsed,
        cpu_s,
        proc.stdout,
        proc.stderr.decode("utf-8", "replace"),
        proc.returncode,
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def read_json(path: Path) -> object:
    return json.loads(path.read_text())


def reset_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)


def tool_environment() -> dict[str, str]:
    env = os.environ.copy()
    local_deps = Path("/home/hj/userfeedback/uid4-0042/.deps")
    if local_deps.exists():
        current = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(local_deps) + (os.pathsep + current if current else "")
    return env
