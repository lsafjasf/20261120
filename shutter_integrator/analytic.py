"""Exact analytic shutter integrals for axis-aligned moving rects.

For a rect [x0 + v t, x0 + v t + w] and a pixel [px_lo, px_hi], the
coverage c(t) is piecewise linear in t with kinks where rect edges
cross pixel edges.  The shutter weight w(t) is piecewise linear with
its own kinks, so the product integrates exactly piece by piece.
This gives an independent ground truth for the numeric integrator.
"""

from __future__ import annotations

import numpy as np

from .weights import ShutterWeight, total_weight


def _coverage(t, px_lo, px_hi, x0, v, w):
    lo = x0 + v * t
    return max(0.0, min(px_hi, lo + w) - max(px_lo, lo))


def rect_coverage_integral(
    px_lo,
    px_hi,
    x0,
    v,
    rect_w,
    t_open,
    t_close,
    weight: ShutterWeight,
):
    """Normalized shutter integral of rect coverage for one pixel.

    Returns Phi in [0, 1]: the weight-averaged fraction of the pixel
    covered by the rect over the shutter interval.
    """
    pts = {t_open, t_close}
    if v != 0.0:
        for edge in (px_lo, px_hi):
            pts.add((edge - x0) / v)
            pts.add((edge - x0 - rect_w) / v)
    pts.update(weight.kinks(t_open, t_close))
    bounds = [t_open]
    bounds.extend(sorted(p for p in pts if t_open < p < t_close))
    bounds.append(t_close)
    total = 0.0
    for p0, p1 in zip(bounds[:-1], bounds[1:]):
        if p1 <= p0:
            continue
        w0 = weight(p0, t_open, t_close)
        w1 = weight(p1, t_open, t_close)
        c0 = _coverage(p0, px_lo, px_hi, x0, v, rect_w)
        c1 = _coverage(p1, px_lo, px_hi, x0, v, rect_w)
        dw = w1 - w0
        dc = c1 - c0
        total += (p1 - p0) * (
            w0 * c0 + 0.5 * (w0 * dc + c0 * dw) + dw * dc / 3.0
        )
    return total / total_weight(weight, t_open, t_close)


def rect_coverage_profile(
    x_edges,
    x0,
    v,
    rect_w,
    t_open,
    t_close,
    weight: ShutterWeight,
):
    """Phi for every pixel column given pixel edges."""
    x_edges = np.asarray(x_edges, dtype=np.float64)
    return np.array(
        [
            rect_coverage_integral(
                lo, hi, x0, v, rect_w, t_open, t_close, weight
            )
            for lo, hi in zip(x_edges[:-1], x_edges[1:])
        ]
    )
