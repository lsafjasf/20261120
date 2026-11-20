"""Shutter weight functions with exact integration over sub-intervals.

Weights are non-negative functions of time defined on the shutter
interval [t_open, t_close].  Each weight exposes its interior kink
points so that :func:`integrate_weight` can integrate piecewise-linear
weights exactly with the trapezoid rule.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Sequence


@dataclass(frozen=True)
class ShutterWeight:
    """A shutter weighting function w(t) on [t_open, t_close]."""

    name: str
    fn: Callable[[float, float, float], float]
    kink_fn: Callable[[float, float], Sequence[float]] = field(
        default=lambda a, b: ()
    )

    def __call__(self, t: float, t_open: float, t_close: float) -> float:
        return self.fn(t, t_open, t_close)

    def kinks(self, t_open: float, t_close: float) -> Sequence[float]:
        return self.kink_fn(t_open, t_close)


def _uniform_fn(t: float, t_open: float, t_close: float) -> float:
    return 1.0 if t_open <= t <= t_close else 0.0


def _triangle_fn(t: float, t_open: float, t_close: float) -> float:
    if not (t_open <= t <= t_close):
        return 0.0
    mid = 0.5 * (t_open + t_close)
    half = 0.5 * (t_close - t_open)
    if half <= 0.0:
        return 0.0
    return max(0.0, 1.0 - abs(t - mid) / half)


def _triangle_kinks(t_open: float, t_close: float) -> Sequence[float]:
    return (0.5 * (t_open + t_close),)


UNIFORM = ShutterWeight("uniform", _uniform_fn)
TRIANGLE = ShutterWeight("triangle", _triangle_fn, _triangle_kinks)

_REGISTRY = {w.name: w for w in (UNIFORM, TRIANGLE)}


def get_weight(name: str) -> ShutterWeight:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise ValueError(
            f"unknown shutter weight {name!r}; "
            f"available: {sorted(_REGISTRY)}"
        ) from None


def integrate_weight(
    weight: ShutterWeight,
    t_open: float,
    t_close: float,
    x0: float,
    x1: float,
) -> float:
    """Integrate w(t) over [x0, x1] clipped to the shutter interval.

    Exact for piecewise-linear weights: the interval is split at the
    weight's kink points and each linear piece is integrated with the
    trapezoid rule.
    """
    lo = max(x0, t_open)
    hi = min(x1, t_close)
    if hi <= lo:
        return 0.0
    pts = [lo]
    pts.extend(k for k in weight.kinks(t_open, t_close) if lo < k < hi)
    pts.append(hi)
    total = 0.0
    for p0, p1 in zip(pts[:-1], pts[1:]):
        w0 = weight(p0, t_open, t_close)
        w1 = weight(p1, t_open, t_close)
        total += 0.5 * (w0 + w1) * (p1 - p0)
    return total


def total_weight(weight: ShutterWeight, t_open: float, t_close: float) -> float:
    """Integral of w(t) over the whole shutter interval."""
    return integrate_weight(weight, t_open, t_close, t_open, t_close)
