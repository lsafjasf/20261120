from __future__ import annotations

from fractions import Fraction
from typing import Iterable

from .model import APNGError, FrameControl, FrameTiming, TimingPlan


WEBP_MAX_FRAME_DURATION_MS = 0xFFFFFF


def _round_half_up(value: Fraction) -> int:
    return (value.numerator * 2 + value.denominator) // (
        value.denominator * 2
    )


def _fraction_text(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator}/{value.denominator}"


def plan_delays(controls: Iterable[FrameControl]) -> TimingPlan:
    """Map exact APNG delays onto cumulative integer-millisecond timestamps.

    Rounding the cumulative clock, rather than every delay independently,
    keeps fractional delays from drifting.  A frame that would round to a
    zero-length slot is assigned one millisecond and reported explicitly.
    """

    timings: list[FrameTiming] = []
    diagnostics: list[dict[str, object]] = []
    exact_cumulative = Fraction(0)
    previous_ms = 0

    for index, control in enumerate(controls):
        exact_delay = control.delay_ms
        exact_cumulative += exact_delay
        if exact_delay < 0:
            raise APNGError(f"frame {index} has a negative delay")
        rounded_cumulative = _round_half_up(exact_cumulative)
        lost_precision = rounded_cumulative <= previous_ms
        cumulative_ms = (
            previous_ms + 1 if lost_precision else rounded_cumulative
        )
        duration_ms = cumulative_ms - previous_ms
        if duration_ms > WEBP_MAX_FRAME_DURATION_MS:
            raise APNGError(
                f"frame {index} delay exceeds WebP's 24-bit duration limit"
            )
        if lost_precision:
            diagnostics.append(
                {
                    "code": "time_granularity_loss",
                    "frame_index": index,
                    "frame_number": index + 1,
                    "exact_delay_ms": float(exact_delay),
                    "exact_delay": _fraction_text(exact_delay),
                    "rounded_cumulative_ms": rounded_cumulative,
                    "previous_cumulative_ms": previous_ms,
                    "assigned_duration_ms": duration_ms,
                    "message": (
                        f"frame {index + 1} has {_fraction_text(exact_delay)}"
                        " ms delay; one millisecond was reserved to keep it"
                    ),
                }
            )
        timings.append(
            FrameTiming(
                index=index,
                exact_delay_ms=exact_delay,
                duration_ms=duration_ms,
                cumulative_ms=cumulative_ms,
                lost_precision=lost_precision,
            )
        )
        previous_ms = cumulative_ms

    return TimingPlan(
        frames=timings,
        total_ms=previous_ms,
        diagnostics=diagnostics,
    )
