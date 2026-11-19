"""Smoothing filters for noisy device-orientation streams.

Two update policies are supported:

* ``DirectFilter``  - one-to-one mapping, every sensor sample is applied.
* ``OneEuroFilter`` - adaptive low-pass: heavy smoothing while the head is
  still (kills sensor jitter) and near-zero smoothing during fast motion
  (so a quick head turn is tracked without perceptible lag).

All angles are radians and differences are wrapped to ``[-pi, pi)``.
"""

import math

TAU = math.tau


def wrap_angle(angle: float) -> float:
    """Wrap an angle to the half-open interval ``[-pi, pi)``."""
    return (angle + math.pi) % TAU - math.pi


def angle_diff(a: float, b: float) -> float:
    """Shortest signed angular distance ``a - b``."""
    return wrap_angle(a - b)


def _alpha(cutoff_hz: float, dt: float) -> float:
    """EMA coefficient for a given 1-pole cutoff and sample interval."""
    return 1.0 / (1.0 + 1.0 / (TAU * cutoff_hz * dt))


class DirectFilter:
    """Passthrough filter: output equals the raw sensor angle."""

    name = "direct"

    def __init__(self) -> None:
        self.last_t = None
        self.last_x = None

    def reset(self) -> None:
        self.last_t = None
        self.last_x = None

    def __call__(self, t: float, x: float) -> float:
        self.last_t = t
        self.last_x = x
        return wrap_angle(x)


class OneEuroFilter:
    """Angle-aware One-Euro filter (Casiez et al., CHI 2012).

 Parameters
    ----------
    min_cutoff:
        Low-pass cutoff at rest. Lower = more jitter rejection.
    beta:
        Cutoff slope w.r.t. angular speed. Higher = less lag on fast moves.
    d_cutoff:
        Cutoff used to smooth the angular-velocity estimate.
    """

    name = "one_euro"

    def __init__(
        self,
        min_cutoff: float = 1.0,
        beta: float = 0.6,
        d_cutoff: float = 1.0,
    ) -> None:
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self.reset()

    def reset(self) -> None:
        self._t_prev = None
        self._x_prev = None
        self._dx_prev = 0.0
        self.last_cutoff = self.min_cutoff
        self.last_velocity = 0.0

    def __call__(self, t: float, x: float) -> float:
        x = wrap_angle(x)
        if self._t_prev is None:
            self._t_prev = t
            self._x_prev = x
            return x

        dt = max(t - self._t_prev, 1e-4)
        dx = angle_diff(x, self._x_prev) / dt

        alpha_d = _alpha(self.d_cutoff, dt)
        dx_hat = alpha_d * dx + (1.0 - alpha_d) * self._dx_prev

        cutoff = self.min_cutoff + self.beta * abs(dx_hat)
        alpha = _alpha(cutoff, dt)
        x_hat = self._x_prev + alpha * angle_diff(x, self._x_prev)
        x_hat = wrap_angle(x_hat)

        self._t_prev = t
        self._x_prev = x_hat
        self._dx_prev = dx_hat
        self.last_cutoff = cutoff
        self.last_velocity = dx_hat
        return x_hat
