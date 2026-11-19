"""Generate simulated tape captures: stereo program + pilot tone track.

A virtual tape deck plays the program at instantaneous speed 1 + delta(t),
where delta combines slow wow and fast flutter. The recorded sample at
tape-time t equals the source evaluated at program-time
u(t) = integral of (1 + delta). Ground-truth maps are kept for scoring.
"""

from dataclasses import dataclass, field

import numpy as np

FS = 48000
PILOT_FREQ = 3150.0  # Hz, standard wow/flutter reference tone


@dataclass
class SpeedProfile:
    """Components of the fractional speed deviation delta(t)."""

    wow_hz: float = 0.5
    wow_depth: float = 0.004       # 0.4 % slow wow
    drift_hz: float = 0.08
    drift_depth: float = 0.002     # 0.2 % very slow drift
    flutter1_hz: float = 9.0
    flutter1_depth: float = 0.0012
    flutter2_hz: float = 13.0
    flutter2_depth: float = 0.0005
    # Extra deviation on the right program channel only, emulating
    # head-skew jitter that one shared map cannot remove.
    skew_hz: float = 0.3
    skew_depth: float = 0.0004
    skew_offset: float = 0.0

    def delta(self, t: np.ndarray) -> np.ndarray:
        return (
            self.wow_depth * np.sin(2 * np.pi * self.wow_hz * t)
            + self.drift_depth * np.sin(2 * np.pi * self.drift_hz * t + 0.7)
            + self.flutter1_depth * np.sin(2 * np.pi * self.flutter1_hz * t + 1.3)
            + self.flutter2_depth * np.sin(2 * np.pi * self.flutter2_hz * t + 2.1)
        )

    def delta_right_extra(self, t: np.ndarray) -> np.ndarray:
        return self.skew_offset + self.skew_depth * np.sin(
            2 * np.pi * self.skew_hz * t + 0.4
        )


@dataclass
class Capture:
    fs: int
    left: np.ndarray
    right: np.ndarray
    pilot: np.ndarray
    gaps: list = field(default_factory=list)  # (start_s, end_s) dropouts
    time_map_left: np.ndarray = None   # ground-truth program-time per sample
    time_map_right: np.ndarray = None
    clean_left: np.ndarray = None      # source program on uniform grid
    clean_right: np.ndarray = None
    profile: SpeedProfile = None


def _program_source(u: np.ndarray) -> tuple:
    """Deterministic stereo 'programme' evaluated at program-time u."""
    left = (
        0.50 * np.sin(2 * np.pi * 220.0 * u)
        + 0.30 * np.sin(2 * np.pi * 440.0 * u + 0.5)
        + 0.15 * np.sin(2 * np.pi * 660.0 * u + 1.1)
    )
    left *= 0.8 + 0.2 * np.sin(2 * np.pi * 0.25 * u)
    right = (
        0.50 * np.sin(2 * np.pi * 330.0 * u + 0.3)
        + 0.25 * np.sin(2 * np.pi * 550.0 * u + 0.9)
    )
    right *= 0.8 + 0.2 * np.sin(2 * np.pi * 0.20 * u + 1.0)
    # Shared 1 kHz reference tone used for L/R alignment measurement.
    ref = 0.20 * np.sin(2 * np.pi * 1000.0 * u)
    return left + ref, right + ref


def simulate_capture(duration_s: float = 12.0, fs: int = FS,
                     gaps=((4.0, 4.6), (8.0, 8.9)),
                     profile: SpeedProfile = None,
                     seed: int = 7) -> Capture:
    profile = profile or SpeedProfile()
    n = int(round(duration_s * fs))
    t = np.arange(n) / fs

    def time_map(delta):
        return t + np.concatenate(([0.0], np.cumsum(delta[:-1]))) / fs

    delta_l = profile.delta(t)
    delta_r = delta_l + profile.delta_right_extra(t)
    u_left = time_map(delta_l)
    u_right = time_map(delta_r)

    clean_left, clean_right = _program_source(t)
    left = _program_source(u_left)[0]
    right = _program_source(u_right)[1]

    pilot = 0.6 * np.sin(2 * np.pi * PILOT_FREQ * u_left)
    rng = np.random.default_rng(seed)
    left += 1e-4 * rng.standard_normal(n)
    right += 1e-4 * rng.standard_normal(n)
    pilot += 1e-4 * rng.standard_normal(n)

    for start_s, end_s in gaps:
        i0, i1 = int(start_s * fs), int(end_s * fs)
        fade = int(0.005 * fs)
        pilot[i0:i1] = 0.0
        pilot[i0 - fade:i0] *= np.linspace(1.0, 0.0, fade)
        pilot[i1:i1 + fade] *= np.linspace(0.0, 1.0, fade)

    return Capture(fs=fs, left=left, right=right, pilot=pilot,
                   gaps=list(gaps), time_map_left=u_left,
                   time_map_right=u_right, clean_left=clean_left,
                   clean_right=clean_right, profile=profile)
