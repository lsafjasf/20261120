"""Synthesize LED-wall test clips and control samples.

Simulates a rolling-scan LED wall filmed by a camera with a given
exposure time, frame rate and trigger phase, and writes PNG frame
sequences plus a shoot log (JSON) describing every take.
"""

from __future__ import annotations

import json
import os

import imageio.v2 as imageio
import numpy as np

HEIGHT = 256
WIDTH = 384
BASE_GRAY = 140.0


def _lit_fraction(pos: np.ndarray, t0: float, t_exp: float,
                  scan_period: float, duty: float, phase0: float) -> np.ndarray:
    """Fraction of the exposure window during which each scan line is lit.

    ``pos`` is the normalized line position (0..1) along the scan axis.
    A line is lit when ``frac(t / scan_period - phase0 - pos) < duty``.
    Integrated numerically over the exposure window [t0, t0 + t_exp].
    """
    n_sub = 256
    dt = t_exp / n_sub
    t = t0 + (np.arange(n_sub) + 0.5) * dt
    ph = (t[None, :] / scan_period - phase0 - pos[:, None]) % 1.0
    return (ph < duty).mean(axis=1)


def render_scan_clip(n_frames: int, fps: float, exposure: float,
                     trigger_phase: float, scan_period: float, duty: float,
                     scan_axis: str = "row", noise: float = 1.0,
                     seed: int = 0) -> np.ndarray:
    """Render a uniform-gray wall with rolling-scan banding."""
    rng = np.random.default_rng(seed)
    n_lines = HEIGHT if scan_axis == "row" else WIDTH
    pos = (np.arange(n_lines) + 0.5) / n_lines
    frames = np.zeros((n_frames, HEIGHT, WIDTH), dtype=np.float64)
    for k in range(n_frames):
        t0 = k / fps + trigger_phase
        lit = _lit_fraction(pos, t0, exposure, scan_period, duty, 0.0)
        line_val = BASE_GRAY * (0.35 + 0.65 * lit)
        if scan_axis == "row":
            frames[k] = line_val[:, None]
        else:
            frames[k] = line_val[None, :]
    frames += rng.normal(0.0, noise, size=frames.shape)
    return np.clip(frames, 0, 255)


def render_gradient(n_frames: int, noise: float = 1.0, seed: int = 1) -> np.ndarray:
    """Control: static vertical gradient, no scan banding."""
    rng = np.random.default_rng(seed)
    ramp = np.linspace(60, 220, HEIGHT)[:, None]
    frames = np.broadcast_to(ramp, (n_frames, HEIGHT, WIDTH)).copy()
    frames += rng.normal(0.0, noise, size=frames.shape)
    return np.clip(frames, 0, 255)


def render_moving_object(n_frames: int, noise: float = 1.0, seed: int = 2) -> np.ndarray:
    """Control: uniform wall + dark rectangle moving downward (real motion)."""
    rng = np.random.default_rng(seed)
    frames = np.full((n_frames, HEIGHT, WIDTH), BASE_GRAY)
    box_h, box_w = 40, 60
    for k in range(n_frames):
        top = int((k * 4) % (HEIGHT + box_h)) - box_h
        left = WIDTH // 3
        r0, r1 = max(0, top), min(HEIGHT, top + box_h)
        if r1 > r0:
            frames[k, r0:r1, left:left + box_w] = 45.0
    frames += rng.normal(0.0, noise, size=frames.shape)
    return np.clip(frames, 0, 255)


def render_static_bands(n_frames: int, period_rows: int = 32,
                        noise: float = 1.0, seed: int = 3) -> np.ndarray:
    """Control: static periodic horizontal stripes (e.g. wall texture)."""
    rng = np.random.default_rng(seed)
    stripe = np.where((np.arange(HEIGHT) // (period_rows // 2)) % 2 == 0,
                      BASE_GRAY + 30, BASE_GRAY - 30)
    frames = np.broadcast_to(stripe[:, None], (n_frames, HEIGHT, WIDTH)).copy()
    frames += rng.normal(0.0, noise, size=frames.shape)
    return np.clip(frames, 0, 255)


def write_take(out_dir: str, name: str, frames: np.ndarray, log_entry: dict) -> None:
    """Write one take as a PNG sequence and append its log entry."""
    take_dir = os.path.join(out_dir, name)
    os.makedirs(os.path.join(take_dir, "frames"), exist_ok=True)
    for k, fr in enumerate(frames):
        imageio.imwrite(os.path.join(take_dir, "frames", f"frame_{k:03d}.png"),
                        fr.astype(np.uint8))
    log_entry.update({"take": name, "frames": int(frames.shape[0]),
                      "height": int(frames.shape[1]), "width": int(frames.shape[2])})
    with open(os.path.join(take_dir, "log.json"), "w") as fh:
        json.dump(log_entry, fh, indent=2, ensure_ascii=False)


def synthesize_dataset(out_dir: str, n_frames: int = 48) -> list:
    """Build the full demo dataset: scan directions + controls + log."""
    os.makedirs(out_dir, exist_ok=True)
    fps = 48.0
    takes = []

    def scan_take(name, fps, exposure, trigger_phase, scan_hz, duty, axis, note):
        frames = render_scan_clip(n_frames, fps, exposure, trigger_phase,
                                  1.0 / scan_hz, duty, scan_axis=axis)
        entry = {"fps": fps, "exposure_s": exposure,
                 "trigger_phase_s": trigger_phase, "kind": "scan_test",
                 "note": note}
        write_take(out_dir, name, frames, entry)
        takes.append(entry)

    # Vertical scan, band drifts downward ~6 rows/frame.
    scan_take("scan_down", fps, 1 / 9600, 0.0, 48 * (20 + 6 / 256), 0.8, "row",
              "vertical scan, downward drifting band")
    # Vertical scan, band drifts upward.
    scan_take("scan_up", fps, 1 / 9600, 0.0, 48 * (20 - 6 / 256), 0.8, "row",
              "vertical scan, upward drifting band")
    # Horizontal scans (column bands).
    scan_take("scan_right", fps, 1 / 9600, 0.0, 48 * (20 + 6 / 384), 0.8, "col",
              "horizontal scan, rightward drifting band")
    scan_take("scan_left", fps, 1 / 9600, 0.0, 48 * (20 - 6 / 384), 0.8, "col",
              "horizontal scan, leftward drifting band")
    # Same wall, longer exposure: candidates overlap -> ambiguity demo.
    scan_take("scan_down_exp2", fps, 1 / 4800, 0.0, 48 * (20 + 6 / 256), 0.8, "row",
              "same wall, longer exposure: candidate scan rates overlap")

    controls = [
        ("ctrl_gradient", render_gradient(n_frames), "static gradient background"),
        ("ctrl_moving_object", render_moving_object(n_frames), "real object motion"),
        ("ctrl_static_bands", render_static_bands(n_frames), "static periodic stripes"),
    ]
    for name, frames, note in controls:
        entry = {"fps": fps, "exposure_s": 1 / 240, "trigger_phase_s": 0.0,
                 "kind": "control", "note": note}
        write_take(out_dir, name, frames, entry)
        takes.append(entry)

    with open(os.path.join(out_dir, "shoot_log.json"), "w") as fh:
        json.dump(takes, fh, indent=2, ensure_ascii=False)
    return takes
