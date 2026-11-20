"""Synthetic validation scenes with deterministic references.

All scenes are generated analytically with exact per-pixel coverage,
so no renderer is needed.  Reference integrations use dense time
sampling through the same quadrature, which keeps the replay fully
deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from .integrator import integrate_frames
from .weights import ShutterWeight, UNIFORM
from .analytic import rect_coverage_profile


def _box_coverage(x_edges, lo, hi):
    """Overlap length of [lo, hi] with pixel cells given their edges."""
    left = np.maximum(x_edges[:-1], lo)
    right = np.minimum(x_edges[1:], hi)
    return np.clip(right - left, 0.0, None)


@dataclass
class MovingBarScene:
    """Unit-brightness bar of bar_width px moving at velocity px/t."""

    width: int = 128
    height: int = 32
    bar_width: float = 4.0
    velocity: float = 8.0
    x0: float = 8.0
    row0: int = 8
    row1: int = 24

    def frame_at(self, t: float) -> np.ndarray:
        x_edges = np.arange(self.width + 1, dtype=np.float64)
        lo = self.x0 + self.velocity * t
        cov = _box_coverage(x_edges, lo, lo + self.bar_width)
        frame = np.zeros((self.height, self.width, 4), dtype=np.float32)
        rows = slice(self.row0, self.row1)
        frame[rows, :, 0] = cov[None, :].astype(np.float32)
        frame[rows, :, 1] = cov[None, :].astype(np.float32)
        frame[rows, :, 2] = cov[None, :].astype(np.float32)
        frame[rows, :, 3] = cov[None, :].astype(np.float32)
        return frame

    def frames(self, times: Sequence[float]) -> np.ndarray:
        return np.stack([self.frame_at(t) for t in times])

    def expected_streak(self, t_open: float, t_close: float) -> float:
        """Full support of the streak for any a.e.-positive weight."""
        return self.bar_width + abs(self.velocity) * (t_close - t_open)

    def analytic_reference(
        self, t_open: float, t_close: float, weight: ShutterWeight
    ) -> np.ndarray:
        """Exact integrated frame from the analytic coverage profile."""
        x_edges = np.arange(self.width + 1, dtype=np.float64)
        phi = rect_coverage_profile(
            x_edges, self.x0, self.velocity, self.bar_width,
            t_open, t_close, weight,
        )
        frame = np.zeros((self.height, self.width, 4), dtype=np.float32)
        rows = slice(self.row0, self.row1)
        frame[rows, :, 0] = phi[None, :]
        frame[rows, :, 1] = phi[None, :]
        frame[rows, :, 2] = phi[None, :]
        frame[rows, :, 3] = phi[None, :]
        return frame


@dataclass
class OcclusionScene:
    """Semi-transparent foreground rect sweeping a static background."""

    width: int = 96
    height: int = 32
    fg_width: float = 12.0
    velocity: float = 6.0
    x0: float = 4.0
    bg_rgb: tuple = (0.1, 0.4, 0.8)
    bg_alpha: float = 0.35
    fg_rgb: tuple = (1.0, 0.6, 0.1)
    fg_alpha: float = 0.5
    row0: int = 6
    row1: int = 26

    def frame_at(self, t: float) -> np.ndarray:
        x_edges = np.arange(self.width + 1, dtype=np.float64)
        lo = self.x0 + self.velocity * t
        cov = _box_coverage(x_edges, lo, lo + self.fg_width)
        m = np.zeros((self.height, self.width, 1))
        m[self.row0 : self.row1, :, 0] = cov
        bg_pm, over_pm = self._layers()
        out = bg_pm[None, None, :] * (1.0 - m) + over_pm[None, None, :] * m
        return out.astype(np.float32)

    def _layers(self):
        """Premultiplied (background, fg-over-bg) layer constants."""
        ab = self.bg_alpha
        af = self.fg_alpha
        bg_pm = np.array(
            [self.bg_rgb[0] * ab, self.bg_rgb[1] * ab,
             self.bg_rgb[2] * ab, ab],
            dtype=np.float64,
        )
        over_pm = np.array(
            [
                self.fg_rgb[0] * af + bg_pm[0] * (1.0 - af),
                self.fg_rgb[1] * af + bg_pm[1] * (1.0 - af),
                self.fg_rgb[2] * af + bg_pm[2] * (1.0 - af),
                af + ab * (1.0 - af),
            ],
            dtype=np.float64,
        )
        return bg_pm, over_pm

    def frames(self, times: Sequence[float]) -> np.ndarray:
        return np.stack([self.frame_at(t) for t in times])

    def analytic_reference(
        self, t_open: float, t_close: float, weight: ShutterWeight
    ) -> np.ndarray:
        """Exact integrated frame: bg*(1-Phi) + (fg over bg)*Phi."""
        x_edges = np.arange(self.width + 1, dtype=np.float64)
        phi = rect_coverage_profile(
            x_edges, self.x0, self.velocity, self.fg_width,
            t_open, t_close, weight,
        )
        m = np.zeros((self.height, self.width, 1))
        m[self.row0 : self.row1, :, 0] = phi
        bg_pm, over_pm = self._layers()
        out = bg_pm[None, None, :] * (1.0 - m) + over_pm[None, None, :] * m
        return out.astype(np.float32)


@dataclass
class StaticScene:
    """Constant premultiplied color, incl. partial alpha."""

    width: int = 48
    height: int = 24
    rgba: tuple = (0.6, 0.3, 0.15, 0.7)  # straight color + alpha

    def frame_at(self, t: float) -> np.ndarray:
        r, g, b, a = self.rgba
        frame = np.zeros((self.height, self.width, 4), dtype=np.float32)
        frame[..., 0] = r * a
        frame[..., 1] = g * a
        frame[..., 2] = b * a
        frame[..., 3] = a
        return frame

    def frames(self, times: Sequence[float]) -> np.ndarray:
        return np.stack([self.frame_at(t) for t in times])


def dense_reference(
    scene,
    t_open: float,
    t_close: float,
    weight: ShutterWeight = UNIFORM,
    steps: int = 4001,
) -> np.ndarray:
    """Deterministic ground truth via dense sampling of the scene."""
    span = t_close - t_open
    times = np.linspace(
        t_open + 0.5 * span / steps, t_close - 0.5 * span / steps, steps
    )
    nominal_dt = span / steps
    frames = scene.frames(times)
    return integrate_frames(
        frames, times, t_open, t_close, weight, nominal_dt=nominal_dt
    ).frame
