"""MPEG-DASH event timing and period-boundary validation."""

from .model import EventKey, Period, Presentation, ScheduledEvent
from .mpd import parse_mpd
from .scheduler import EventTimeline, RunResult
from .timing import UtcTimingResolver

__all__ = [
    "EventKey",
    "EventTimeline",
    "Period",
    "Presentation",
    "RunResult",
    "ScheduledEvent",
    "UtcTimingResolver",
    "parse_mpd",
]
