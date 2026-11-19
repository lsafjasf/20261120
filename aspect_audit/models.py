from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


LAYOUT_LABELS = {
    "full": "16:9 full frame",
    "letterbox": "letterbox",
    "pillarbox": "pillarbox",
    "fourside": "four-side black",
    "black": "full-frame black",
    "fade": "fade / low-light",
    "unknown": "undetermined",
}


@dataclass
class Rect:
    x: int
    y: int
    width: int
    height: int

    @property
    def aspect(self) -> float:
        return self.width / self.height if self.height else 0.0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass
class FrameResult:
    frame: int
    time: float
    layout: str
    rect: Rect
    aspect: float
    confidence: float
    brightness: float
    scene_brightness: float
    dark_ratio: float
    bars: dict[str, int]
    bar_types: list[str] = field(default_factory=list)
    edge_contrast: dict[str, float] = field(default_factory=dict)
    subtitle_edges: list[str] = field(default_factory=list)
    subtitle_confidence: float = 0.0
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["rect"] = self.rect.as_dict()
        data["aspect"] = round(self.aspect, 4)
        data["confidence"] = round(self.confidence, 4)
        data["brightness"] = round(self.brightness, 2)
        data["scene_brightness"] = round(self.scene_brightness, 2)
        data["dark_ratio"] = round(self.dark_ratio, 4)
        data["subtitle_confidence"] = round(self.subtitle_confidence, 4)
        return data


@dataclass
class Event:
    kind: str
    start_frame: int
    end_frame: int
    start_time: float
    end_time: float
    confidence: float
    detail: str = ""
    edge: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "start_frame": self.start_frame,
            "end_frame": self.end_frame,
            "start_time": round(self.start_time, 4),
            "end_time": round(self.end_time, 4),
            "confidence": round(self.confidence, 4),
            "detail": self.detail,
            **({"edge": self.edge} if self.edge else {}),
        }


@dataclass
class Segment:
    layout: str
    bar_types: list[str]
    aspect: float
    canonical_aspect: str
    start_frame: int
    end_frame: int
    start_time: float
    end_time: float
    rect: Rect
    confidence: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "layout": self.layout,
            "label": LAYOUT_LABELS.get(self.layout, self.layout),
            "bar_types": self.bar_types,
            "aspect": round(self.aspect, 4),
            "canonical_aspect": self.canonical_aspect,
            "start_frame": self.start_frame,
            "end_frame": self.end_frame,
            "start_time": round(self.start_time, 4),
            "end_time": round(self.end_time, 4),
            "rect": self.rect.as_dict(),
            "confidence": round(self.confidence, 4),
        }


@dataclass
class Mismatch:
    start_frame: int
    end_frame: int
    start_time: float
    end_time: float
    detected_aspect: float | None
    declared_aspect: float | None
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "start_frame": self.start_frame,
            "end_frame": self.end_frame,
            "start_time": round(self.start_time, 4),
            "end_time": round(self.end_time, 4),
            "detected_aspect": (
                round(self.detected_aspect, 4)
                if self.detected_aspect is not None
                else None
            ),
            "declared_aspect": (
                round(self.declared_aspect, 4)
                if self.declared_aspect is not None
                else None
            ),
            "reason": self.reason,
        }
