"""Feedback-delay-network reverb laboratory."""

from .engine import FDNConfig, FDNReverb, RoomPreset
from .matrices import build_feedback_matrix, orthogonality_error

__all__ = [
    "FDNConfig",
    "FDNReverb",
    "RoomPreset",
    "build_feedback_matrix",
    "orthogonality_error",
]
