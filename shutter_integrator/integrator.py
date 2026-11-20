"""Core shutter integration over instantaneous photon subframes.

Each subframe is an instantaneous sample at time t_i.  Samples are
treated as piecewise-constant over their Voronoi cells (midpoint
boundaries between neighbours), clipped to the shutter interval.  The
effective weight of sample i is the integral of the shutter weight
over its clipped cell, which makes the quadrature exact for static
content and robust to non-uniform or missing sample times.

When ``nominal_dt`` is given, cells never extend further than half
the nominal sample period beyond their sample time.  A missing
subframe therefore leaves an uncovered gap in the shutter interval
instead of being silently absorbed by its neighbours, and the gap
shows up as reduced coverage.  Without ``nominal_dt`` the cells are
pure Voronoi cells, which is the better quadrature for genuinely
non-uniform sampling.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np

from .weights import ShutterWeight, UNIFORM, integrate_weight, total_weight


def cell_boundaries(
    times: Sequence[float], nominal_dt: Optional[float] = None
) -> tuple[np.ndarray, np.ndarray]:
    """Voronoi cell boundaries [left_i, right_i] for sample times.

    When nominal_dt is given, cells are clipped to +/- nominal_dt/2
    around their sample so that missing samples leave measurable
    gaps; otherwise cells tile the whole sampled span.
    """
    t = np.asarray(times, dtype=np.float64)
    n = t.shape[0]
    if n == 0:
        raise ValueError("at least one sample time is required")
    if np.any(np.diff(t) <= 0.0):
        raise ValueError("sample times must be strictly increasing")
    if n == 1:
        half = 0.5 * (1.0 if nominal_dt is None else float(nominal_dt))
        return np.array([t[0] - half]), np.array([t[0] + half])
    left = np.empty(n)
    right = np.empty(n)
    left[1:] = 0.5 * (t[:-1] + t[1:])
    right[:-1] = left[1:]
    left[0] = t[0] - 0.5 * (t[1] - t[0])
    right[-1] = t[-1] + 0.5 * (t[-1] - t[-2])
    if nominal_dt is not None:
        nominal_dt = float(nominal_dt)
        if nominal_dt <= 0.0:
            raise ValueError("nominal_dt must be positive")
        half = 0.5 * nominal_dt
        np.maximum(left, t - half, out=left)
        np.minimum(right, t + half, out=right)
    return left, right


@dataclass
class IntegrationResult:
    """Result of integrating one output frame."""

    frame: np.ndarray  # (H, W, 4) float32 premultiplied linear
    sample_weights: np.ndarray  # effective weight per input sample
    coverage: float  # fraction of shutter weight covered by samples
    weight_sum: float  # sum of effective sample weights


def integrate_frames(
    frames: np.ndarray,
    times: Sequence[float],
    t_open: float,
    t_close: float,
    weight: ShutterWeight = UNIFORM,
    nominal_dt: Optional[float] = None,
) -> IntegrationResult:
    """Integrate premultiplied linear subframes over the shutter.

    frames: (N, H, W, C) float array of instantaneous samples.
    times:  N strictly increasing sample times.
    Returns the normalized weighted integral; static content is
    reproduced exactly regardless of shutter length or weight shape.
    """
    if t_close <= t_open:
        raise ValueError("shutter close must be after shutter open")
    frames = np.asarray(frames)
    if frames.ndim < 3:
        raise ValueError("frames must have shape (N, H, W, C)")
    times = list(times)
    if frames.shape[0] != len(times):
        raise ValueError("frames and times length mismatch")

    left, right = cell_boundaries(times, nominal_dt)
    w = np.array(
        [
            integrate_weight(weight, t_open, t_close, lo, hi)
            for lo, hi in zip(left, right)
        ],
        dtype=np.float64,
    )
    w_sum = float(w.sum())
    if w_sum <= 0.0:
        raise ValueError("no sample cell overlaps the shutter interval")

    acc = np.tensordot(w, frames.astype(np.float64), axes=(0, 0))
    out = (acc / w_sum).astype(np.float32)
    coverage = w_sum / total_weight(weight, t_open, t_close)
    return IntegrationResult(out, w, min(coverage, 1.0), w_sum)
