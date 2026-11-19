# -*- coding: utf-8 -*-
"""Data model: intervals, description clips, placements, results."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Interval:
    """Half-open time interval [start, end) in seconds."""

    start: float
    end: float
    label: str = ""

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"bad interval [{self.start}, {self.end})")

    @property
    def duration(self) -> float:
        return self.end - self.start

    def overlaps(self, other: "Interval") -> bool:
        return self.start < other.end and other.start < self.end

    def contains(self, other: "Interval") -> bool:
        return self.start <= other.start and other.end <= self.end


@dataclass
class DescriptionClip:
    """One narration segment with a legal time window and two takes."""

    clip_id: str
    window_start: float
    window_end: float
    long_duration: float
    short_duration: float
    importance: int = 1
    text: str = ""
    locked_start: Optional[float] = None
    locked_version: Optional[str] = None  # "long" | "short"

    def __post_init__(self) -> None:
        if self.window_end < self.window_start:
            raise ValueError(f"{self.clip_id}: bad window")
        if not 0 < self.short_duration <= self.long_duration:
            raise ValueError(f"{self.clip_id}: bad durations")
        if (self.locked_start is None) != (self.locked_version is None):
            raise ValueError(f"{self.clip_id}: lock needs start and version")
        if self.locked_version not in (None, "long", "short"):
            raise ValueError(f"{self.clip_id}: bad locked version")

    @property
    def locked(self) -> bool:
        return self.locked_start is not None

    def duration_of(self, version: str) -> float:
        return self.long_duration if version == "long" else self.short_duration


@dataclass
class Placement:
    """A scheduled narration: chosen version and start time."""

    clip: DescriptionClip
    version: str  # "long" | "short"
    start: float
    locked: bool = False

    @property
    def end(self) -> float:
        return self.start + self.clip.duration_of(self.version)

    @property
    def interval(self) -> Interval:
        return Interval(self.start, self.end, self.clip.clip_id)


@dataclass
class Rejection:
    """Why a clip was not placed (or why a choice was downgraded)."""

    clip_id: str
    reason: str


@dataclass
class Timeline:
    """Input timeline: dialogue, sfx keep-zones and description clips."""

    duration: float
    dialogue: list[Interval] = field(default_factory=list)
    sfx_zones: list[Interval] = field(default_factory=list)
    clips: list[DescriptionClip] = field(default_factory=list)

    @property
    def blocked(self) -> list[Interval]:
        return sorted(self.dialogue + self.sfx_zones, key=lambda i: i.start)


@dataclass
class ScheduleResult:
    placements: list[Placement] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    score: tuple = ()

    def placement_for(self, clip_id: str) -> Optional[Placement]:
        for p in self.placements:
            if p.clip.clip_id == clip_id:
                return p
        return None
