from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from fractions import Fraction
from pathlib import Path
from typing import Any


class DiagnosticCode(str, Enum):
    MISSING_REEL = "MISSING_REEL"
    REFERENCE_MISMATCH = "REFERENCE_MISMATCH"
    CHECKSUM_FAILURE = "CHECKSUM_FAILURE"
    PLAYABLE_TIME_OVERFLOW = "PLAYABLE_TIME_OVERFLOW"


class AssetKind(str, Enum):
    PICTURE = "picture"
    SOUND = "sound"
    SUBTITLE = "subtitle"
    CAPTION = "caption"
    OTHER = "other"

    @property
    def short_label(self) -> str:
        return {
            AssetKind.PICTURE: "P",
            AssetKind.SOUND: "S",
            AssetKind.SUBTITLE: "T",
            AssetKind.CAPTION: "C",
            AssetKind.OTHER: "?",
        }[self]


@dataclass(frozen=True)
class EditRate:
    numerator: int
    denominator: int = 1

    def __post_init__(self) -> None:
        if self.numerator <= 0 or self.denominator <= 0:
            raise ValueError("edit rate must be positive")

    @property
    def fps(self) -> Fraction:
        return Fraction(self.numerator, self.denominator)

    def seconds(self, frames: int) -> Fraction:
        return Fraction(frames, 1) / self.fps

    def __str__(self) -> str:
        if self.denominator == 1:
            return str(self.numerator)
        return f"{self.numerator}/{self.denominator}"

    @classmethod
    def parse(cls, text: str | None) -> EditRate:
        if not text or not text.strip():
            raise ValueError("empty edit rate")
        parts = text.split()
        if len(parts) == 1:
            numerator = int(parts[0])
            denominator = 1
        elif len(parts) == 2:
            numerator, denominator = int(parts[0]), int(parts[1])
        else:
            raise ValueError(f"invalid edit rate: {text!r}")
        return cls(numerator, denominator)


@dataclass
class AssetChunk:
    path: str
    volume_index: int | None = None


@dataclass
class AssetMapEntry:
    asset_id: str
    chunks: list[AssetChunk] = field(default_factory=list)
    packing_list: bool = False

    @property
    def path(self) -> str:
        return self.chunks[0].path if self.chunks else ""

    @property
    def volume_index(self) -> int | None:
        return self.chunks[0].volume_index if self.chunks else None


@dataclass
class PackingAsset:
    asset_id: str
    path: str
    size: int | None = None
    hash_value: str | None = None
    hash_algorithm: str | None = None
    mime_type: str | None = None
    annotation: str | None = None


@dataclass
class PackingList:
    pkl_id: str
    path: Path
    assets: dict[str, PackingAsset] = field(default_factory=dict)
    annotation: str | None = None
    issuer: str | None = None
    issue_date: str | None = None


@dataclass
class CplResource:
    kind: AssetKind
    asset_id: str
    edit_rate: EditRate | None
    intrinsic_duration: int | None
    entry_point: int | None
    duration: int | None
    annotation: str | None = None


@dataclass
class CplReel:
    reel_id: str
    resources: list[CplResource] = field(default_factory=list)


@dataclass
class CompositionPlaylist:
    cpl_id: str
    path: Path
    title: str
    content_kind: str | None
    reels: list[CplReel] = field(default_factory=list)
    annotation: str | None = None


@dataclass
class AssetMap:
    path: Path
    entries: dict[str, AssetMapEntry] = field(default_factory=dict)


@dataclass
class PackageDocuments:
    root: Path
    asset_map: AssetMap | None
    packing_lists: list[PackingList]
    cpls: list[CompositionPlaylist]
    errors: list[Diagnostic] = field(default_factory=list)
    base_roots: list[Path] = field(default_factory=list)
    inherited_asset_maps: list[AssetMap] = field(default_factory=list)
    inherited_packing_lists: list[PackingList] = field(default_factory=list)


@dataclass
class TimelineSegment:
    reel_id: str
    kind: AssetKind
    asset_id: str
    start: Fraction
    end: Fraction
    edit_rate: EditRate | None
    entry_point: int | None
    duration_frames: int | None
    intrinsic_frames: int | None
    path: str | None
    volume: str | None
    inherited: bool = False
    error_codes: list[DiagnosticCode] = field(default_factory=list)

    @property
    def duration(self) -> Fraction:
        return max(Fraction(0), self.end - self.start)


@dataclass
class Diagnostic:
    code: DiagnosticCode
    message: str
    reel: str | None = None
    asset_id: str | None = None
    asset_path: str | None = None
    volume: str | None = None
    screening_time: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "code": self.code.value,
            "message": self.message,
            "reel": self.reel,
            "asset_id": self.asset_id,
            "asset_path": self.asset_path,
            "volume": self.volume,
            "screening_time": self.screening_time,
        }
        if self.details:
            result["details"] = self.details
        return result


@dataclass
class InspectionReport:
    root: Path
    cpl: CompositionPlaylist | None
    diagnostics: list[Diagnostic] = field(default_factory=list)
    timeline: list[TimelineSegment] = field(default_factory=list)
    total_duration: Fraction = Fraction(0)
    checked_assets: int = 0
    checked_bytes: int = 0
    edit_rates: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.diagnostics

    @property
    def error_count(self) -> int:
        return len(self.diagnostics)

    def counts_by_code(self) -> dict[str, int]:
        counts = {code.value: 0 for code in DiagnosticCode}
        for diagnostic in self.diagnostics:
            counts[diagnostic.code.value] += 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "root": str(self.root),
            "cpl": (
                {
                    "id": self.cpl.cpl_id,
                    "title": self.cpl.title,
                    "path": str(self.cpl.path),
                    "reels": [
                        {
                            "id": reel.reel_id,
                            "assets": [
                                {
                                    "kind": resource.kind.value,
                                    "id": resource.asset_id,
                                    "edit_rate": (
                                        str(resource.edit_rate)
                                        if resource.edit_rate
                                        else None
                                    ),
                                    "intrinsic_duration": resource.intrinsic_duration,
                                    "entry_point": resource.entry_point,
                                    "duration": resource.duration,
                                }
                                for resource in reel.resources
                            ],
                        }
                        for reel in self.cpl.reels
                    ],
                }
                if self.cpl
                else None
            ),
            "total_duration_seconds": float(self.total_duration),
            "total_duration_timecode": format_time(self.total_duration),
            "edit_rates": self.edit_rates,
            "timeline": [
                {
                    "reel": segment.reel_id,
                    "kind": segment.kind.value,
                    "asset_id": segment.asset_id,
                    "start_seconds": float(segment.start),
                    "end_seconds": float(segment.end),
                    "start_timecode": format_time(segment.start),
                    "end_timecode": format_time(segment.end),
                    "edit_rate": (
                        str(segment.edit_rate) if segment.edit_rate else None
                    ),
                    "entry_point": segment.entry_point,
                    "duration_frames": segment.duration_frames,
                    "intrinsic_frames": segment.intrinsic_frames,
                    "path": segment.path,
                    "volume": segment.volume,
                    "inherited": segment.inherited,
                    "error_codes": [code.value for code in segment.error_codes],
                }
                for segment in self.timeline
            ],
            "checked_assets": self.checked_assets,
            "checked_bytes": self.checked_bytes,
            "counts": self.counts_by_code(),
            "errors": [diagnostic.to_dict() for diagnostic in self.diagnostics],
        }


def format_time(seconds: Fraction | float | int) -> str:
    value = Fraction(seconds).limit_denominator(1_000_000)
    total_ms = int(value * 1000)
    sign = "-" if total_ms < 0 else ""
    total_ms = abs(total_ms)
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{sign}{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"
