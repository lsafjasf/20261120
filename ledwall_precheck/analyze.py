"""Per-line brightness analysis and scan-band detection.

For every take the frames are reduced to per-row (and per-column)
mean brightness profiles. A temporal high-pass removes static
structure (gradients, fixed stripes); remaining localized dips are
tracked across frames to measure band width, drift direction and
cross-frame period. Column-coverage separates full-width scan bands
from real moving objects.
"""

from __future__ import annotations

import glob
import json
import os

import imageio.v2 as imageio
import numpy as np

DARK_DELTA = 0.08        # depth threshold on temporally high-passed profile
MIN_COVERAGE = 0.6       # fraction of columns a scan band must span
STATIC_VELOCITY = 0.2    # lines/frame below which a feature is "static"


def load_take(take_dir: str) -> tuple[np.ndarray, dict]:
    with open(os.path.join(take_dir, "log.json")) as fh:
        log = json.load(fh)
    paths = sorted(glob.glob(os.path.join(take_dir, "frames", "*.png")))
    frames = np.stack([imageio.imread(p).astype(np.float64) for p in paths])
    if frames.ndim == 4:  # drop alpha/RGB if present
        frames = frames[..., :3].mean(axis=-1)
    return frames, log


def line_profiles(frames: np.ndarray, axis: str) -> np.ndarray:
    """Mean brightness per line per frame; shape (n_frames, n_lines)."""
    return frames.mean(axis=2) if axis == "row" else frames.mean(axis=1)


def _segments(mask: np.ndarray) -> list[tuple[int, int]]:
    """Contiguous True runs in a 1-D mask, as (start, end) inclusive."""
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return []
    splits = np.where(np.diff(idx) > 1)[0]
    groups = np.split(idx, splits + 1)
    return [(int(g[0]), int(g[-1])) for g in groups]


def _column_coverage(frame: np.ndarray, rows: tuple[int, int],
                     axis: str, depth_abs: float) -> float:
    """Fraction of columns where candidate band lines are dark."""
    r0, r1 = rows
    strip = frame[r0:r1 + 1, :] if axis == "row" else frame[:, r0:r1 + 1].T
    median = np.median(frame)
    return float((strip < median - depth_abs).mean())


def detect_bands(frames: np.ndarray, axis: str,
                 dark_delta: float = DARK_DELTA) -> dict:
    """Detect drifting dark bands along one axis."""
    prof = line_profiles(frames, axis)
    n_frames, n_lines = prof.shape
    norm = prof / np.median(prof, axis=1, keepdims=True)
    highpass = norm - norm.mean(axis=0, keepdims=True)  # removes static structure

    # Robust noise from adjacent-line differences: band edges cover a small
    # fraction of lines, so the MAD of first differences stays clean even
    # when the band itself occupies many lines.
    diff = np.diff(highpass, axis=1)
    noise = float(np.median(np.abs(diff - np.median(diff))) * 1.4826 / np.sqrt(2))
    thresh = -max(dark_delta, 4 * noise)
    depth_abs = float(np.median(prof) * max(dark_delta, 4 * noise))

    detections = []  # (frame, centroid, width, coverage)
    for k in range(n_frames):
        for r0, r1 in _segments(highpass[k] < thresh):
            width = r1 - r0 + 1
            if width < 2:
                continue
            seg = highpass[k, r0:r1 + 1]
            centroid = r0 + float(np.average(np.arange(width), weights=-seg))
            cov = _column_coverage(frames[k], (r0, r1), axis, depth_abs)
            detections.append({"frame": k, "centroid": centroid,
                               "width": width, "coverage": cov,
                               "depth": float(-seg.min())})
    if not detections:
        return {"axis": axis, "status": "none", "detections": [],
                "highpass": highpass, "norm": norm}

    coverage = float(np.mean([d["coverage"] for d in detections]))
    ks = np.array([d["frame"] for d in detections], dtype=float)
    cs = np.array([d["centroid"] for d in detections], dtype=float)
    # unwrap centroid jumps larger than half the frame height
    cs_un = cs.copy()
    for i in range(1, len(cs_un)):
        while cs_un[i] - cs_un[i - 1] > n_lines / 2:
            cs_un[i] -= n_lines
        while cs_un[i] - cs_un[i - 1] < -n_lines / 2:
            cs_un[i] += n_lines
    velocity = float(np.polyfit(ks, cs_un, 1)[0]) if len(ks) > 1 else 0.0
    width = float(np.mean([d["width"] for d in detections]))
    depth = float(np.mean([d["depth"] for d in detections]))

    if coverage < MIN_COVERAGE:
        status = "localized_object"
    elif abs(velocity) < STATIC_VELOCITY:
        status = "static_structure"
    else:
        status = "scan_band"
    period_frames = (n_lines / abs(velocity)) if abs(velocity) > 1e-9 else None
    return {"axis": axis, "status": status, "detections": detections,
            "velocity_lines_per_frame": velocity,
            "direction": ("down" if velocity > 0 else "up") if axis == "row"
                         else ("right" if velocity > 0 else "left"),
            "band_width_lines": width, "mean_depth": depth,
            "coverage": coverage, "period_frames": period_frames,
            "highpass": highpass, "norm": norm}


def scan_rate_candidates(velocity: float, n_lines: int, fps: float,
                         f_min: float = 500.0, f_max: float = 20000.0) -> dict:
    """All scan frequencies consistent with the observed drift (aliasing).

    A wall scanning at ``f`` Hz across ``n_lines`` lines drifts by
    ``n_lines * (f / fps mod 1)`` lines per frame (signed). Every integer
    alias ``f = fps * (n +/- v / n_lines)`` reproduces the observation.
    Returns a compact residue-class summary plus the full list.
    """
    frac = (velocity / n_lines) % 1.0
    residues = sorted({round(fps * frac, 3), round(fps * (1 - frac), 3)})
    cands = []
    n = 0
    while True:
        added = False
        for f in (fps * (n + frac), fps * (n + (1 - frac))):
            if f_min <= f <= f_max:
                cands.append(round(f, 2))
                added = True
        if fps * (n + 1) > f_max and not added:
            break
        n += 1
        if n > 1000:
            break
    cands = sorted(set(cands))
    return {"residues_mod_fps": residues, "fps": fps,
            "f_min": f_min, "f_max": f_max, "count": len(cands),
            "frequencies_hz": cands}


def disambiguation_advice(candidates: dict, fps: float, exposure: float) -> list:
    """Suggest re-shoot conditions that separate aliased candidates."""
    tips = []
    if candidates["count"] > 1:
        tips.append(
            f"补拍一条 fps={fps + 1:.3f}（或 {fps - 1:.3f}）的测试片："
            "不同候选扫描频率在新帧率下的漂移速度不同，可唯一确定。")
        tips.append(
            f"补拍一条曝光 {exposure / 2:.6f}s 与 {exposure * 2:.6f}s 的对比条："
            "条带宽度随曝光/扫描占空比变化，可约束扫描周期。")
    return tips


def analyze_take(take_dir: str) -> dict:
    """Full analysis of one take; returns a JSON-serializable result."""
    frames, log = load_take(take_dir)
    axes = {}
    best = None
    for axis in ("row", "col"):
        res = detect_bands(frames, axis)
        axes[axis] = res
        if res["status"] == "scan_band" and (
                best is None or res["mean_depth"] > best["mean_depth"]):
            best = res
    result = {"take": log["take"], "log": log, "axes": {}}
    for axis, res in axes.items():
        result["axes"][axis] = {k: v for k, v in res.items()
                                if k not in ("highpass", "norm", "detections")}
        result["axes"][axis]["n_detections"] = len(res["detections"])
    if best is not None:
        n_lines = frames.shape[1] if best["axis"] == "row" else frames.shape[2]
        cands = scan_rate_candidates(best["velocity_lines_per_frame"],
                                     n_lines, log["fps"])
        result["verdict"] = "scan_band"
        result["primary_axis"] = best["axis"]
        result["scan_rate_candidates"] = cands
        result["ambiguous"] = cands["count"] > 1
        result["reshoot_advice"] = disambiguation_advice(
            cands, log["fps"], log["exposure_s"])
        depth = best["mean_depth"]
        result["risk"] = "high" if depth > 0.15 else "medium"
    else:
        statuses = {a["status"] for a in axes.values()}
        result["verdict"] = ("localized_object" if "localized_object" in statuses
                             else "static_structure" if "static_structure" in statuses
                             else "clean")
        result["risk"] = "low"
        result["ambiguous"] = False
    # keep arrays for the report stage without breaking JSON output
    result["_frames"] = frames
    result["_axes_full"] = axes
    return result
