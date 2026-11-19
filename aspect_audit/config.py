from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class AuditConfig:
    analysis_width: int = 480
    min_bar_px: int = 4
    dark_level: int = 28
    black_mean: float = 10.0
    black_dark_ratio: float = 0.985
    fade_peak: float = 70.0
    fade_min_frames: int = 3
    min_layout_frames: int = 3
    boundary_tolerance_px: int = 8
    aspect_tolerance: float = 0.035
    subtitle_min_bright_fraction: float = 0.012
    subtitle_max_band_fraction: float = 0.34
    subtitle_min_luma_delta: float = 28.0

    def to_dict(self) -> dict:
        return asdict(self)
