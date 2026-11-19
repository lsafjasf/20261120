from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable


@dataclass(frozen=True, order=True)
class EventKey:
    """A DASH event identity used to detect cross-Period repetition."""

    scheme_id_uri: str
    value: str
    event_id: int


@dataclass(frozen=True)
class EventSignal:
    key: EventKey
    period_id: str
    presentation_time: int
    duration: int
    timescale: int
    presentation_time_offset: int
    message_data: str
    ordinal: int


@dataclass(frozen=True)
class EventStream:
    scheme_id_uri: str
    value: str
    timescale: int
    presentation_time_offset: int
    events: tuple[EventSignal, ...]


@dataclass(frozen=True)
class Period:
    id: str
    start: float
    duration: float
    event_streams: tuple[EventStream, ...] = ()

    @property
    def end(self) -> float:
        return self.start + self.duration

    def contains(self, media_time: float) -> bool:
        """Periods use half-open intervals, [start, end)."""
        return self.start <= media_time < self.end


@dataclass(frozen=True)
class UtcTiming:
    scheme_id_uri: str
    value: str


@dataclass(frozen=True)
class Presentation:
    id: str
    availability_start_time: datetime
    duration: float
    periods: tuple[Period, ...]
    utc_timings: tuple[UtcTiming, ...] = ()

    def period_at(self, media_time: float) -> Period:
        for period in self.periods:
            if period.contains(media_time):
                return period
        raise ValueError(f"no Period contains media time {media_time:g}s")

    def period_by_id(self, period_id: str) -> Period:
        for period in self.periods:
            if period.id == period_id:
                return period
        raise KeyError(period_id)


@dataclass(frozen=True)
class ScheduledEvent:
    """An Event aligned from Period-local ticks to global media time."""

    key: EventKey
    start: float
    end: float
    duration: float
    message_data: str
    owner_period_id: str
    signals: tuple[EventSignal, ...]

    @property
    def declared_period_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(signal.period_id for signal in self.signals))

    @property
    def instantaneous(self) -> bool:
        return self.duration == 0


@dataclass
class EventRuntime:
    event: ScheduledEvent
    trigger_count: int = 0
    first_trigger_at: float | None = None
    last_trigger_at: float | None = None
    expired_at: float | None = None
    inherited_at: float | None = None
    inherited_from: str | None = None
    post_switch_triggers: int = 0


def unique_sorted(values: Iterable[float]) -> tuple[float, ...]:
    return tuple(sorted(set(values)))
