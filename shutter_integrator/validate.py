"""Validation suite: streak length, occlusion, static invariance.

Runs the synthetic scenes through the integrator, compares against
the exact analytic references, writes per-frame error maps and a
JSON report.  Also verifies that missing subframes flag the affected
output frames.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

from .imageio import error_map
from .integrator import integrate_frames
from .manifest import Manifest, OutputFrame, SubFrame
from .scenes import MovingBarScene, OcclusionScene, StaticScene
from .weights import TRIANGLE, UNIFORM

WEIGHTS = (UNIFORM, TRIANGLE)


def measure_support_width(profile: np.ndarray, rel_threshold: float = 1e-3):
    """Sub-pixel width of the region above a relative threshold."""
    profile = np.asarray(profile, dtype=np.float64)
    peak = profile.max()
    if peak <= 0:
        return 0.0
    thr = peak * rel_threshold
    above = np.nonzero(profile > thr)[0]
    if len(above) == 0:
        return 0.0
    lo_i, hi_i = above[0], above[-1]
    lo = float(lo_i)
    if lo_i > 0:
        p0, p1 = profile[lo_i - 1], profile[lo_i]
        if p1 > p0:
            lo = lo_i - 1 + (thr - p0) / (p1 - p0)
    hi = float(hi_i)
    if hi_i < len(profile) - 1:
        p0, p1 = profile[hi_i], profile[hi_i + 1]
        if p0 > p1:
            hi = hi_i + (p0 - thr) / (p0 - p1)
    return hi - lo


def _subframe_times(k: int, n_sub: int) -> np.ndarray:
    return k + (np.arange(n_sub) + 0.5) / n_sub


def _check(name: str, passed: bool, detail: dict) -> dict:
    return {"name": name, "passed": bool(passed), **detail}


def check_static_invariance() -> dict:
    """Static frames: shutter length/weight must not change output."""
    scene = StaticScene()
    ref = scene.frame_at(0.0)
    times = _subframe_times(0, 5)
    frames = scene.frames(times)
    worst = 0.0
    for w in WEIGHTS:
        for dur in (0.2, 0.5, 1.0):
            res = integrate_frames(frames, times, 0.3, 0.3 + dur, w)
            worst = max(worst, float(np.abs(res.frame - ref).max()))
    return _check(
        "static_invariance", worst < 1e-6, {"max_abs_diff": worst}
    )


def check_streak_length(n_frames: int = 4, n_sub: int = 9,
                        tol: float = 0.25) -> dict:
    """Moving bright bar: measured streak must match the analytic one.

    Both the integrator output and the exact analytic reference are
    pixel-averaged, so their support widths (measured with the same
    sub-pixel threshold) must agree.  The theoretical continuous
    support w + v*T is reported for context.
    """
    scene = MovingBarScene()
    rows = slice(scene.row0, scene.row1)
    per_frame = []
    ok = True
    for k in range(n_frames):
        times = _subframe_times(k, n_sub)
        frames = scene.frames(times)
        for w in WEIGHTS:
            res = integrate_frames(frames, times, k, k + 1.0, w)
            ref = scene.analytic_reference(k, k + 1.0, w)
            profile = res.frame[rows, :, 0].max(axis=0)
            ref_profile = ref[rows, :, 0].max(axis=0)
            measured = measure_support_width(profile)
            analytic = measure_support_width(ref_profile)
            expected = scene.expected_streak(k, k + 1.0)
            err = abs(measured - analytic)
            ok = ok and err <= tol
            per_frame.append(
                {
                    "frame": k,
                    "weight": w.name,
                    "measured": round(measured, 3),
                    "analytic": round(analytic, 3),
                    "theoretical": round(expected, 3),
                    "abs_err": round(err, 3),
                }
            )
    return _check("streak_length", ok, {"frames": per_frame})


def _scene_error_maps(scene, name: str, out_dir: Path, n_frames: int,
                      n_sub: int, tol: float) -> dict:
    """Integrate a scene per output frame, emit error maps + stats."""
    err_dir = out_dir / "errormaps" / name
    err_dir.mkdir(parents=True, exist_ok=True)
    per_frame = []
    ok = True
    for k in range(n_frames):
        times = _subframe_times(k, n_sub)
        frames = scene.frames(times)
        for w in WEIGHTS:
            res = integrate_frames(frames, times, k, k + 1.0, w)
            ref = scene.analytic_reference(k, k + 1.0, w)
            diff = np.abs(res.frame.astype(np.float64) - ref)
            max_err = float(diff.max())
            rmse = float(np.sqrt((diff ** 2).mean()))
            ok = ok and max_err <= tol
            Image.fromarray(
                error_map(res.frame, ref, scale=50.0)
            ).save(err_dir / f"frame_{k:04d}_{w.name}.png")
            per_frame.append(
                {
                    "frame": k,
                    "weight": w.name,
                    "max_err": round(max_err, 6),
                    "rmse": round(rmse, 8),
                    "error_map": str(
                        err_dir / f"frame_{k:04d}_{w.name}.png"
                    ),
                }
            )
    return _check(f"{name}_vs_analytic", ok, {"frames": per_frame})


def check_missing_subframes() -> dict:
    """Dropped subframes must flag their output frame and coverage."""
    times = list(_subframe_times(0, 9))
    subs = [SubFrame(time=t, path=f"sub_{i:02d}.npy") for i, t in enumerate(times)]
    dropped = subs[4]
    frame = OutputFrame(
        index=7,
        t_open=0.0,
        t_close=1.0,
        subframes=[s for s in subs if s is not dropped],
        expected_times=times,
    )
    manifest = Manifest(frames=[frame])
    flagged = manifest.flagged_frames()
    scene = MovingBarScene()
    kept_times = [s.time for s in frame.subframes]
    res = integrate_frames(
        scene.frames(kept_times), kept_times, 0.0, 1.0, UNIFORM,
        nominal_dt=1.0 / 9,
    )
    ok = flagged == [7] and res.coverage < 1.0
    return _check(
        "missing_subframe_flag",
        ok,
        {
            "flagged_frames": flagged,
            "missing_times": frame.missing_times(),
            "coverage": round(res.coverage, 4),
        },
    )


def run_validation(out_dir, n_frames: int = 4, n_sub: int = 9,
                   tol: float = 0.02) -> dict:
    """Run all checks, write error maps and report.json."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    checks = [
        check_static_invariance(),
        check_streak_length(n_frames, n_sub),
        _scene_error_maps(MovingBarScene(), "bar", out_dir, n_frames, n_sub, tol),
        _scene_error_maps(
            OcclusionScene(), "occlusion", out_dir, n_frames, n_sub, tol
        ),
        check_missing_subframes(),
    ]
    report = {
        "n_frames": n_frames,
        "n_subframes": n_sub,
        "tolerance": tol,
        "checks": checks,
        "all_passed": all(c["passed"] for c in checks),
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2))
    return report
