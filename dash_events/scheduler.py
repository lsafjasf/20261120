from __future__ import annotations

from dataclasses import dataclass

from .model import (
    EventKey,
    EventRuntime,
    Presentation,
    ScheduledEvent,
)
from .timeline import align_events

_EPSILON = 1e-9


@dataclass(frozen=True)
class PeriodTransition:
    at: float
    from_period_id: str
    to_period_id: str
    inherited_keys: tuple[EventKey, ...]


@dataclass(frozen=True)
class Dispatch:
    key: EventKey
    start: float
    dispatched_at: float
    dispatch_period_id: str
    owner_period_id: str
    inherited: bool
    late_by: float


@dataclass(frozen=True)
class RunResult:
    dispatches: tuple[Dispatch, ...]
    transitions: tuple[PeriodTransition, ...]
    states: dict[EventKey, EventRuntime]

    def dispatch_count(self, key: EventKey) -> int:
        return sum(dispatch.key == key for dispatch in self.dispatches)

    def dispatched_keys(self) -> tuple[EventKey, ...]:
        return tuple(dispatch.key for dispatch in self.dispatches)


class EventTimeline:
    """Dispatch DASH events exactly once while tracking Period ownership."""

    def __init__(self, presentation: Presentation, start: float = 0.0) -> None:
        if not (0.0 <= start < presentation.duration):
            raise ValueError("start time must be inside the Presentation")
        self.presentation = presentation
        self.events = align_events(presentation)
        self.events_by_key = {event.key: event for event in self.events}
        self.states = {
            event.key: EventRuntime(event=event) for event in self.events
        }
        self._events_by_declared_period: dict[str, list[ScheduledEvent]] = {}
        for event in self.events:
            for period_id in event.declared_period_ids:
                self._events_by_declared_period.setdefault(period_id, []).append(event)

        self.current_period = presentation.period_at(start)
        self._last_time = start
        self._initialized = False
        self._dispatches: list[Dispatch] = []
        self._transitions: list[PeriodTransition] = []
        self._switch_time = (
            presentation.periods[1].start
            if len(presentation.periods) > 1
            else presentation.duration
        )

    def _mark_transition(self, new_period_id: str, boundary: float) -> None:
        old_period_id = self.current_period.id
        inherited: list[EventKey] = []
        for state in self.states.values():
            event = state.event
            if state.trigger_count and state.expired_at is None and event.end > boundary:
                state.inherited_at = boundary
                state.inherited_from = old_period_id
                inherited.append(event.key)
            if state.expired_at is None and event.end <= boundary + _EPSILON:
                state.expired_at = event.end
        self._transitions.append(
            PeriodTransition(
                at=boundary,
                from_period_id=old_period_id,
                to_period_id=new_period_id,
                inherited_keys=tuple(inherited),
            )
        )
        self.current_period = self.presentation.period_by_id(new_period_id)

    def _move_to_period(self, media_time: float) -> None:
        if media_time >= self.presentation.duration - _EPSILON:
            target = self.presentation.periods[-1]
        else:
            target = self.presentation.period_at(media_time)
        if target.id == self.current_period.id:
            return
        periods = self.presentation.periods
        current_index = next(
            index for index, period in enumerate(periods) if period.id == self.current_period.id
        )
        target_index = next(
            index for index, period in enumerate(periods) if period.id == target.id
        )
        if target_index < current_index:
            raise ValueError("media time cannot move to an earlier Period")
        for index in range(current_index, target_index):
            self._mark_transition(periods[index + 1].id, periods[index + 1].start)

    def _candidate_events(self) -> list[ScheduledEvent]:
        candidates = {
            event.key: event
            for event in self._events_by_declared_period.get(
                self.current_period.id, []
            )
        }
        for key, state in self.states.items():
            if state.trigger_count and state.expired_at is None:
                candidates[key] = state.event
        return sorted(candidates.values(), key=lambda event: (event.start, event.key))

    def _dispatch(self, event: ScheduledEvent, now: float) -> None:
        state = self.states[event.key]
        inherited = (
            event.owner_period_id != self.current_period.id
            and event.start < self.current_period.start - _EPSILON
        )
        dispatch = Dispatch(
            key=event.key,
            start=event.start,
            dispatched_at=now,
            dispatch_period_id=self.current_period.id,
            owner_period_id=event.owner_period_id,
            inherited=inherited,
            late_by=max(0.0, now - event.start),
        )
        self._dispatches.append(dispatch)
        state.trigger_count += 1
        state.first_trigger_at = (
            now if state.first_trigger_at is None else state.first_trigger_at
        )
        state.last_trigger_at = now
        if now >= self._switch_time - _EPSILON:
            state.post_switch_triggers += 1
        if event.instantaneous:
            state.expired_at = event.end

    def _process_event(self, event: ScheduledEvent, now: float) -> None:
        state = self.states[event.key]
        if state.expired_at is not None:
            return
        if state.trigger_count:
            if event.end <= now + _EPSILON:
                state.expired_at = event.end
            return
        if event.start > now + _EPSILON:
            return
        if event.instantaneous:
            crossed_this_step = (
                not self._initialized
                and abs(event.start - now) <= _EPSILON
            ) or (
                self._initialized
                and self._last_time < event.start <= now + _EPSILON
            )
            if crossed_this_step:
                self._dispatch(event, now)
            else:
                state.expired_at = event.end
            return
        if now < event.end - _EPSILON:
            self._dispatch(event, now)
        else:
            state.expired_at = event.end

    def _advance_single(self, media_time: float) -> None:
        self._move_to_period(media_time)
        for event in self._candidate_events():
            self._process_event(event, media_time)
        self._last_time = media_time
        self._initialized = True

    def advance_to(self, media_time: float) -> RunResult:
        if media_time < self._last_time - _EPSILON:
            raise ValueError("media time cannot move backwards")
        if media_time > self.presentation.duration + _EPSILON:
            raise ValueError("media time exceeds Presentation duration")
        media_time = min(media_time, self.presentation.duration)
        checkpoints = {self._last_time, media_time}
        checkpoints.update(
            event.start
            for event in self.events
            if self._last_time < event.start < media_time
        )
        checkpoints.update(
            period.start
            for period in self.presentation.periods
            if self._last_time < period.start < media_time
        )
        for checkpoint in sorted(checkpoints):
            self._advance_single(checkpoint)
        return self.result()

    def advance_through(
        self, until: float, step: float = 0.25
    ) -> RunResult:
        if step <= 0:
            raise ValueError("step must be positive")
        until = min(until, self.presentation.duration)
        points = {self._last_time, until}
        points.update(period.start for period in self.presentation.periods)
        for event in self.events:
            points.update((event.start, event.end))
        next_point = self._last_time + step
        while next_point < until:
            points.add(next_point)
            next_point += step
        for point in sorted(
            value
            for value in points
            if self._last_time <= value <= until
        ):
            self.advance_to(point)
        return self.result()

    def result(self) -> RunResult:
        return RunResult(
            dispatches=tuple(self._dispatches),
            transitions=tuple(self._transitions),
            states=dict(self.states),
        )
