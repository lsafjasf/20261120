"""Geometric mapping from head yaw to the stereo sound stage.

Convention: angles are radians, yaw increases counter-clockwise.
A source fixed at world azimuth ``theta_s`` is heard by a listener whose
head yaw is ``yaw`` at relative azimuth

    az = theta_s - yaw

so turning the head left (yaw grows) moves the source toward the right ear,
as it does in real life.
"""

import math

from .filters import wrap_angle

# Speed of sound at ~20 C, m/s.
SPEED_OF_SOUND = 343.0


def relative_azimuth(source_yaw: float, head_yaw: float) -> float:
    """World-frame source azimuth expressed in the head frame."""
    return wrap_angle(source_yaw - head_yaw)


def equal_power_gains(azimuth: float) -> tuple[float, float]:
    """Stereo equal-power pan gains (left, right) for an azimuth.

    ``azimuth = -pi/2`` is fully left, ``+pi/2`` fully right; a constant
    ``gL^2 + gR^2 = 1`` keeps loudness constant across the stage.
    """
    pan = max(-1.0, min(1.0, azimuth / (math.pi / 2.0)))
    angle = pan * math.pi / 4.0
    return math.cos(angle), math.sin(angle)


def doppler_ratio(
    angular_velocity: float,
    source_distance: float,
    azimuth: float,
    sound_speed: float = SPEED_OF_SOUND,
) -> float:
    """Instantaneous rotational Doppler frequency ratio f_heard / f_source.

    The listener's tangential velocity projected onto the source direction
    is ``v_r = -omega * r * sin(az)``; approaching the source raises pitch.
    The ratio stays near 1 for head motion (omega ~ a few rad/s, r ~ 1 m).
    """
    radial_velocity = -angular_velocity * source_distance * math.sin(azimuth)
    return sound_speed / (sound_speed - radial_velocity)


def ratio_to_cents(ratio: float) -> float:
    """Convert a frequency ratio to a pitch offset in cents."""
    return 1200.0 * math.log2(ratio)


class SlewLimiter:
    """Per-sample rate limiter that prevents parameter discontinuities.

    Naively retuning a Doppler pitch on every noisy orientation sample
    produces audible zipper noise / pitch jumps during a fast head turn.
    The limiter constrains the maximum change of the value per second, so
    the rendered parameter always follows a continuous trajectory.
    """

    def __init__(self, max_rate_per_s: float, initial: float = 1.0) -> None:
        self.max_rate = float(max_rate_per_s)
        self.value = float(initial)
        self._t_prev = None

    def reset(self, initial: float = 1.0) -> None:
        self.value = float(initial)
        self._t_prev = None

    def update(self, t: float, target: float) -> float:
        if self._t_prev is None:
            # First sample: treat as a single render period so the limiter
            # never snaps (a snap would itself be an audible zipper jump).
            dt = 1.0 / 120.0
        else:
            dt = max(t - self._t_prev, 0.0)
        self._t_prev = t
        delta = target - self.value
        limit = self.max_rate * dt
        if delta > limit:
            delta = limit
        elif delta < -limit:
            delta = -limit
        self.value += delta
        return self.value
