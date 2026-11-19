"""Subtitle overlay with per-channel priority and deterministic stacking.

Overlay order rules (bottom -> top):
  1. higher-priority channels render on top of lower-priority ones;
  2. within the same priority, the later-starting event renders on top;
  3. remaining ties break by channel name (ascending) for determinism.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SubtitleEvent:
    channel: str
    text: str
    start: float
    end: float

    def active_at(self, t: float) -> bool:
        return self.start <= t < self.end


@dataclass
class SubtitleOverlay:
    priorities: dict[str, int]
    events: list[SubtitleEvent] = field(default_factory=list)

    def add_event(self, event: SubtitleEvent) -> None:
        if event.channel not in self.priorities:
            raise KeyError(f"unregistered channel {event.channel!r}")
        if event.end <= event.start:
            raise ValueError("event end must be after start")
        self.events.append(event)

    def active_events(self, t: float) -> list[SubtitleEvent]:
        return [e for e in self.events if e.active_at(t)]

    def render_order(self, t: float) -> list[SubtitleEvent]:
        """Active events sorted bottom -> top per the priority rules."""
        return sorted(
            self.active_events(t),
            key=lambda e: (self.priorities[e.channel], e.start, e.channel),
        )

    def top_event(self, t: float) -> SubtitleEvent | None:
        order = self.render_order(t)
        return order[-1] if order else None

    def render_frame(self, t: float) -> list[str]:
        """Text lines as stacked on screen, bottom line first."""
        return [f"[{e.channel}] {e.text}" for e in self.render_order(t)]
