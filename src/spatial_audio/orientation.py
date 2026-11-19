"""Synthetic device-orientation event streams.

Real ``deviceorientation`` events arrive at an irregular ~30-100 Hz with
quantisation and gyroscope noise. The simulator reproduces those traits so
the whole pipeline can be exercised deterministically on a headless host.
"""

import math
import random
from dataclasses import dataclass


@dataclass(frozen=True)
class OrientationEvent:
    """One absolute-orientation sample (yaw in radians, time in seconds)."""

    t: float
    yaw: float
    seq: int


class OrientationSimulator:
    """Generates jittery, quantised yaw samples from a true motion profile."""

    def __init__(
        self,
        true_yaw_fn,
        sample_rate_hz: float = 60.0,
        noise_std_rad: float = math.radians(0.8),
        quantize_deg: float = 1.0 / 16.0,
        jitter_s: float = 0.0015,
        seed: int = 20261120,
    ) -> None:
        self.true_yaw_fn = true_yaw_fn
        self.dt = 1.0 / sample_rate_hz
        self.noise_std = noise_std_rad
        self.quantize = math.radians(quantize_deg)
        self.jitter = jitter_s
        self._rng = random.Random(seed)

    def stream(self, duration_s: float):
        """Yield :class:`OrientationEvent` objects over ``duration_s``."""
        seq = 0
        t = 0.0
        while t < duration_s - 1e-12:
            jittered_t = max(0.0, t + self._rng.uniform(-self.jitter, self.jitter))
            raw = self.true_yaw_fn(t) + self._rng.gauss(0.0, self.noise_std)
            yaw = round(raw / self.quantize) * self.quantize
            yield OrientationEvent(jittered_t, yaw, seq)
            seq += 1
            t += self.dt


def fast_turn_profile(
    rest_deg: float = 30.0,
    peak_deg_s: float = 420.0,
    settle_s: float = 1.2,
):
    """Motion profile: rest -> Gaussian velocity fast turn -> rest.

    Returns ``(yaw_fn, velocity_fn)``. The head rotates from
    ``-rest_deg`` to ``+rest_deg``; peak angular speed is
    ``peak_deg_s`` at t = settle_s.
    """
    total = math.radians(2.0 * rest_deg)
    sigma = total / (peak_deg_s * math.pi / 180.0 * math.sqrt(2.0 * math.pi))
    t0 = settle_s

    def velocity(t: float) -> float:
        z = (t - t0) / sigma
        return (total / (sigma * math.sqrt(2.0 * math.pi))) * math.exp(-0.5 * z * z)

    # Integrate velocity numerically once to obtain absolute yaw.
    step = 1e-4
    grid = []
    yaw = -math.radians(rest_deg)
    n = int((settle_s + 5.0 * sigma) / step) + 2
    for i in range(n):
        grid.append((i * step, yaw))
        yaw += velocity(i * step) * step

    def yaw_fn(t: float) -> float:
        if t <= grid[0][0]:
            return grid[0][1]
        if t >= grid[-1][0]:
            return grid[-1][1]
        i = int(t / step)
        if i >= len(grid) - 1:
            return grid[-1][1]
        t0_, y0 = grid[i]
        _, y1 = grid[i + 1]
        frac = (t - t0_) / step
        return y0 + (y1 - y0) * frac

    return yaw_fn, velocity, sigma
