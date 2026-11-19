from __future__ import annotations

from collections import defaultdict

from .model import EventSignal, Presentation, ScheduledEvent

_EPSILON = 1e-9


def signal_start(signal: EventSignal, presentation: Presentation) -> float:
    period = presentation.period_by_id(signal.period_id)
    return period.start + (
        signal.presentation_time - signal.presentation_time_offset
    ) / signal.timescale


def signal_end(signal: EventSignal, presentation: Presentation) -> float:
    return signal_start(signal, presentation) + signal.duration / signal.timescale


def align_events(presentation: Presentation) -> tuple[ScheduledEvent, ...]:
    grouped: dict[tuple[str, str, int], list[EventSignal]] = defaultdict(list)
    for period in presentation.periods:
        for stream in period.event_streams:
            for signal in stream.events:
                grouped[(signal.key.scheme_id_uri, signal.key.value, signal.key.event_id)].append(
                    signal
                )

    aligned: list[ScheduledEvent] = []
    for signals in grouped.values():
        starts = [signal_start(signal, presentation) for signal in signals]
        ends = [signal_end(signal, presentation) for signal in signals]
        start = min(starts)
        end = max(ends)
        if max(starts) - min(starts) > _EPSILON:
            key = signals[0].key
            raise ValueError(
                f"event {key.scheme_id_uri!r}/{key.value!r}/{key.event_id} has "
                f"inconsistent cross-Period start times: {starts}"
            )
        if max(ends) - min(ends) > _EPSILON:
            key = signals[0].key
            raise ValueError(
                f"event {key.scheme_id_uri!r}/{key.value!r}/{key.event_id} has "
                f"inconsistent cross-Period end times: {ends}"
            )
        if not (0.0 <= start < presentation.duration):
            raise ValueError(
                f"event {signals[0].key} starts outside the presentation: {start:g}s"
            )
        owner = presentation.period_at(start)
        aligned.append(
            ScheduledEvent(
                key=signals[0].key,
                start=start,
                end=end,
                duration=end - start,
                message_data=signals[0].message_data,
                owner_period_id=owner.id,
                signals=tuple(sorted(signals, key=lambda signal: signal.ordinal)),
            )
        )
    return tuple(sorted(aligned, key=lambda event: (event.start, event.key)))
