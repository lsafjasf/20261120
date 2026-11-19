from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any


class APNGError(ValueError):
    """Raised when an APNG is structurally invalid or unsupported."""


class WebPError(RuntimeError):
    """Raised when libwebp cannot encode or decode a file."""


@dataclass(frozen=True)
class FrameControl:
    sequence: int
    width: int
    height: int
    x: int
    y: int
    delay_num: int
    delay_den: int
    dispose_op: int
    blend_op: int

    @property
    def delay(self) -> Fraction:
        denominator = self.delay_den or 100
        return Fraction(self.delay_num, denominator)

    @property
    def delay_ms(self) -> Fraction:
        return self.delay * 1000


@dataclass
class APNGFrame:
    index: int
    control: FrameControl
    compressed: bytes
    pixels: bytes = b""


@dataclass
class APNGAnimation:
    path: Path
    width: int
    height: int
    bit_depth: int
    color_type: int
    interlace: int
    frames: list[APNGFrame]
    num_plays: int
    default_image_is_frame: bool
    rendered: list[bytes] = field(default_factory=list)


@dataclass(frozen=True)
class FrameTiming:
    index: int
    exact_delay_ms: Fraction
    duration_ms: int
    cumulative_ms: int
    lost_precision: bool


@dataclass
class TimingPlan:
    frames: list[FrameTiming]
    total_ms: int
    diagnostics: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class Difference:
    pixels: int
    x: int
    y: int
    width: int
    height: int

    @property
    def empty(self) -> bool:
        return self.pixels == 0

    def as_dict(self) -> dict[str, Any]:
        if self.empty:
            return {"pixels": 0, "bbox": None}
        return {
            "pixels": self.pixels,
            "bbox": {
                "x": self.x,
                "y": self.y,
                "width": self.width,
                "height": self.height,
            },
        }


@dataclass
class VerificationResult:
    ok: bool
    source: APNGAnimation
    timing: TimingPlan
    width: int
    height: int
    webp_loop_count: int
    webp_frame_count: int
    webp_durations_ms: list[int]
    webp_timestamps_ms: list[int]
    first_error: dict[str, Any] | None = None
    checks: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "input": str(self.source.path),
            "canvas": {"width": self.width, "height": self.height},
            "source": {
                "frame_count": len(self.source.frames),
                "loop_count": self.source.num_plays,
                "total_duration_ms": self.timing.total_ms,
            },
            "webp": {
                "frame_count": self.webp_frame_count,
                "loop_count": self.webp_loop_count,
                "durations_ms": self.webp_durations_ms,
                "timestamps_ms": self.webp_timestamps_ms,
                "total_duration_ms": sum(self.webp_durations_ms),
            },
            "timing": {
                "durations_ms": [item.duration_ms for item in self.timing.frames],
                "cumulative_ms": [
                    item.cumulative_ms for item in self.timing.frames
                ],
                "diagnostics": self.timing.diagnostics,
            },
            "checks": self.checks,
            "first_error": self.first_error,
        }
