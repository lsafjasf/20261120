"""Device-orientation driven spatial audio toolkit (pure stdlib).

Modules:
    orientation: device rotation event source / head-motion simulator
    filters:     direct mapping vs. One-Euro angle smoothing
    spatial:     relative azimuth, equal-power panning, rotational Doppler
    engine:      event -> filter -> audio-render update pipeline + metrics
    reverb:      RT60-controlled Schroeder reverb with guaranteed smooth tail
"""

from .filters import OneEuroFilter, DirectFilter, wrap_angle, angle_diff
from .spatial import (
    relative_azimuth,
    equal_power_gains,
    doppler_ratio,
    ratio_to_cents,
    SlewLimiter,
)
from .engine import SpatialEngine, RenderState, OrientationEvent
from .reverb import Reverb

__all__ = [
    "OneEuroFilter",
    "DirectFilter",
    "wrap_angle",
    "angle_diff",
    "relative_azimuth",
    "equal_power_gains",
    "doppler_ratio",
    "ratio_to_cents",
    "SlewLimiter",
    "SpatialEngine",
    "RenderState",
    "OrientationEvent",
    "Reverb",
]
