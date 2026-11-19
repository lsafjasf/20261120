from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .media import MediaInfo, iter_frames, probe_video, split_eyes
from .pattern import (
    BG_BOX,
    FG_BOX,
    decode_frame_id,
    decode_label,
    detect_marker,
)


CRITICAL = "critical"
WARNING = "warning"


@dataclass
class AnalysisOptions:
    packing: str = "sbs"
    max_disparity_px: int | None = None
    max_disparity_ratio: float = 0.025
    max_vertical_px: float = 2.0
    assumed_order: str | None = None


@dataclass
class EyeFrame:
    source_frame: int | None
    label: str | None
    label_confidence: float
    foreground: tuple[float, float] | None
    background: tuple[float, float] | None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "source_frame": self.source_frame,
            "label": self.label,
            "label_confidence": round(self.label_confidence, 1),
            "foreground": None,
            "background": None,
        }
        if self.foreground is not None:
            result["foreground"] = [round(self.foreground[0], 2), round(self.foreground[1], 2)]
        if self.background is not None:
            result["background"] = [round(self.background[0], 2), round(self.background[1], 2)]
        return result


@dataclass
class FrameResult:
    index: int
    time_seconds: float
    first: EyeFrame
    second: EyeFrame
    foreground_dx: float | None = None
    foreground_dy: float | None = None
    background_dx: float | None = None
    background_dy: float | None = None
    pair_order: str | None = None
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "time_seconds": round(self.time_seconds, 6),
            "first": self.first.to_dict(),
            "second": self.second.to_dict(),
            "foreground_dx": _round_optional(self.foreground_dx),
            "foreground_dy": _round_optional(self.foreground_dy),
            "background_dx": _round_optional(self.background_dx),
            "background_dy": _round_optional(self.background_dy),
            "pair_order": self.pair_order,
            "flags": self.flags,
        }


def _round_optional(value: float | None) -> float | None:
    return round(value, 2) if value is not None else None


class AnomalyCollector:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []
        self._spans: dict[tuple[Any, ...], dict[str, Any]] = {}

    def span(
        self,
        kind: str,
        severity: str,
        eye: str,
        index: int,
        message: str,
        *,
        signature: Any = None,
        value: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        key = (kind, severity, eye, signature)
        current = self._spans.get(key)
        if current is not None and current["end_frame"] == index - 1:
            current["end_frame"] = index
            current["frame_count"] += 1
            if value is not None:
                current["max_value"] = max(current.get("max_value", 0), abs(value))
            return
        item: dict[str, Any] = {
            "type": kind,
            "severity": severity,
            "eye": eye,
            "start_frame": index,
            "end_frame": index,
            "frame_count": 1,
            "message": message,
        }
        if value is not None:
            item["max_value"] = round(abs(value), 2)
        if details:
            item["details"] = details
        self._spans[key] = item
        self.items.append(item)

    def point(
        self,
        kind: str,
        severity: str,
        eye: str,
        index: int,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        item: dict[str, Any] = {
            "type": kind,
            "severity": severity,
            "eye": eye,
            "start_frame": index,
            "end_frame": index,
            "frame_count": 1,
            "message": message,
        }
        if details:
            item["details"] = details
        self.items.append(item)

    def extend_last(self, end_frame: int) -> None:
        item = self.items[-1]
        item["end_frame"] = end_frame
        item["frame_count"] = end_frame - item["start_frame"] + 1


def _metadata_order(info: MediaInfo) -> tuple[str | None, str | None]:
    values = {key.lower(): str(value).lower() for key, value in info.metadata.items()}
    for key in ("stereo_mode", "stereo_layout", "eye_order", "multiview_layout"):
        value = values.get(key)
        if not value:
            continue
        normalized = value.replace("-", "_").replace(" ", "_")
        if normalized in {"left_right", "lr", "left_first", "ab", "side_by_side_lr"}:
            return "ab", f"metadata:{key}={value}"
        if normalized in {"right_left", "rl", "right_first", "ba", "side_by_side_rl"}:
            return "ba", f"metadata:{key}={value}"
        if normalized in {"top_bottom", "tb", "over_under", "ou_lr"}:
            return "ab", f"metadata:{key}={value}"
        if normalized in {"bottom_top", "bt", "ou_rl"}:
            return "ba", f"metadata:{key}={value}"
    return None, None


def _decode_eye(data: bytes, width: int, height: int) -> EyeFrame:
    label, label_confidence = decode_label(data, width, height)
    source_frame, _ = decode_frame_id(data, width, height)
    return EyeFrame(
        source_frame=source_frame,
        label=label,
        label_confidence=label_confidence,
        foreground=detect_marker(data, width, height, FG_BOX, "red"),
        background=detect_marker(data, width, height, BG_BOX, "green"),
    )


def _delta(
    first: tuple[float, float] | None,
    second: tuple[float, float] | None,
) -> tuple[float | None, float | None]:
    if first is None or second is None:
        return None, None
    return second[0] - first[0], second[1] - first[1]


def analyze_video(
    path: str | Path,
    options: AnalysisOptions | None = None,
    *,
    progress: Callable[[int, int | None], None] | None = None,
    info: MediaInfo | None = None,
) -> dict[str, Any]:
    options = options or AnalysisOptions()
    if options.packing not in {"sbs", "ou"}:
        raise ValueError("packing must be 'sbs' or 'ou'")
    media = info or probe_video(path)
    eye_w, eye_h = _eye_dimensions_from_info(media, options.packing)
    disparity_limit = options.max_disparity_px
    if disparity_limit is None:
        disparity_limit = max(2, round(eye_w * options.max_disparity_ratio))

    frames: list[FrameResult] = []
    anomalies = AnomalyCollector()
    pair_orders: Counter[str] = Counter()
    labeled_pairs = 0
    measured_frames = 0
    identity_unknown_frames = 0
    last_source: dict[str, tuple[int, int]] = {}
    gap_indices: set[int] = set()
    eye_keys = ("first", "second")

    total = media.frame_count
    for index, packed in iter_frames(media):
        first_data, second_data = split_eyes(packed, media.width, media.height, options.packing)
        first = _decode_eye(first_data, eye_w, eye_h)
        second = _decode_eye(second_data, eye_w, eye_h)
        fg_dx, fg_dy = _delta(first.foreground, second.foreground)
        bg_dx, bg_dy = _delta(first.background, second.background)
        frame = FrameResult(
            index=index,
            time_seconds=float(index / media.frame_rate),
            first=first,
            second=second,
            foreground_dx=fg_dx,
            foreground_dy=fg_dy,
            background_dx=bg_dx,
            background_dy=bg_dy,
        )

        if first.label and second.label:
            labeled_pairs += 1
            if first.label == "L" and second.label == "R":
                frame.pair_order = "ab"
                pair_orders["ab"] += 1
            elif first.label == "R" and second.label == "L":
                frame.pair_order = "ba"
                pair_orders["ba"] += 1
            else:
                frame.flags.append("eye_identity_conflict")
        else:
            identity_unknown_frames += 1

        available = 0
        for marker_name, dx, dy in (
            ("foreground", fg_dx, fg_dy),
            ("background", bg_dx, bg_dy),
        ):
            if dx is None or dy is None:
                anomalies.span(
                    "measurement_missing",
                    WARNING,
                    marker_name,
                    index,
                    f"{marker_name.title()} marker could not be measured in both eyes.",
                )
                frame.flags.append(f"{marker_name}_missing")
                continue
            available += 1
            if abs(dx) > disparity_limit:
                frame.flags.append(f"{marker_name}_parallax_exceeded")
                direction = "positive" if dx > 0 else "negative"
                anomalies.span(
                    "parallax_exceeded",
                    CRITICAL,
                    marker_name,
                    index,
                    f"{marker_name.title()} horizontal parallax exceeds {disparity_limit} px.",
                    signature=(disparity_limit, direction),
                    value=dx,
                    details={
                        "limit_px": disparity_limit,
                        "direction": direction,
                    },
                )
            if abs(dy) > options.max_vertical_px:
                frame.flags.append(f"{marker_name}_vertical_offset")
                direction = "positive" if dy > 0 else "negative"
                anomalies.span(
                    "vertical_offset",
                    CRITICAL,
                    marker_name,
                    index,
                    f"{marker_name.title()} vertical offset exceeds {options.max_vertical_px:g} px.",
                    signature=(options.max_vertical_px, direction),
                    value=dy,
                    details={"limit_px": options.max_vertical_px, "direction": direction},
                )
        if available:
            measured_frames += 1

        for key in eye_keys:
            eye = first if key == "first" else second
            if eye.source_frame is None:
                continue
            if key in last_source:
                previous_index, previous_id = last_source[key]
                expected = previous_id + (index - previous_index)
                difference = eye.source_frame - expected
                if difference > 0:
                    frame.flags.append(f"{key}_timeline_gap")
                    gap_indices.add(index)
                    missing = [expected + offset for offset in range(difference)]
                    anomalies.point(
                        "timeline_gap",
                        CRITICAL,
                        key,
                        index,
                        f"{key.title()} eye jumps from frame {previous_id} to {eye.source_frame}; "
                        f"{difference} frame(s) are missing.",
                        details={
                            "previous_source_frame": previous_id,
                            "observed_source_frame": eye.source_frame,
                            "missing_source_frames": missing,
                        },
                    )
                elif difference < 0:
                    frame.flags.append(f"{key}_timeline_reorder")
                    anomalies.point(
                        "timeline_reorder",
                        CRITICAL,
                        key,
                        index,
                        f"{key.title()} eye source frame goes backward or repeats unexpectedly.",
                        details={
                            "previous_source_frame": previous_id,
                            "observed_source_frame": eye.source_frame,
                        },
                    )
            last_source[key] = (index, eye.source_frame)

        if first.source_frame is not None and second.source_frame is not None:
            pair_delta = second.source_frame - first.source_frame
            if pair_delta:
                frame.flags.append("frame_pair_mismatch")
                anomalies.span(
                    "frame_pair_mismatch",
                    CRITICAL,
                    "both",
                    index,
                    "The two eyes carry different source frame numbers; do not auto-align this run.",
                    signature=pair_delta,
                    details={
                        "second_minus_first": pair_delta,
                        "timeline_gap_on_this_frame": index in gap_indices,
                    },
                )
        frames.append(frame)
        if progress:
            progress(len(frames), total)

    return _finalize_report(
        media,
        options,
        frames,
        anomalies,
        pair_orders,
        labeled_pairs,
        measured_frames,
        identity_unknown_frames,
        disparity_limit,
    )


def _eye_dimensions_from_info(info: MediaInfo, packing: str) -> tuple[int, int]:
    if packing == "sbs":
        return info.width // 2, info.height
    return info.width, info.height // 2


def _finalize_report(
    media: MediaInfo,
    options: AnalysisOptions,
    frames: list[FrameResult],
    anomalies: AnomalyCollector,
    pair_orders: Counter[str],
    labeled_pairs: int,
    measured_frames: int,
    identity_unknown_frames: int,
    disparity_limit: int,
) -> dict[str, Any]:
    if not frames:
        raise ValueError("No video frames could be analyzed")
    total = len(frames)
    last = total - 1
    if options.assumed_order not in {None, "ab", "ba"}:
        raise ValueError("assumed_order must be 'ab', 'ba', or None")

    metadata_order, metadata_source = _metadata_order(media)
    dominant_order: str | None = None
    dominant_count = 0
    if pair_orders:
        dominant_order, dominant_count = pair_orders.most_common(1)[0]
    label_coverage = labeled_pairs / total
    label_consistency = dominant_count / labeled_pairs if labeled_pairs else 0.0
    labels_confirmed = (
        labeled_pairs > 0
        and label_coverage >= 0.5
        and label_consistency >= 0.85
    )

    chosen_order: str | None = None
    identity_source = "unconfirmed"
    if labels_confirmed:
        chosen_order = dominant_order
        identity_source = "burned-in-markers"
    elif options.assumed_order:
        chosen_order = options.assumed_order
        identity_source = "operator"
    elif metadata_order:
        chosen_order = metadata_order
        identity_source = metadata_source or "metadata"

    if labels_confirmed and dominant_order == "ba":
        anomalies.span(
            "eye_order_swapped",
            CRITICAL,
            "both",
            0,
            "Packed slot A carries the R eye and slot B carries the L eye; swap eye order.",
            signature="ba",
            details={"detected_order": "ba"},
        )
        anomalies.extend_last(last)
    if labels_confirmed and pair_orders.get("ab", 0) and pair_orders.get("ba", 0):
        anomalies.span(
            "eye_order_inconsistent",
            CRITICAL,
            "both",
            0,
            "Eye labels change order within the clip.",
        )
        anomalies.extend_last(last)
    if chosen_order is None:
        reasons = []
        if not labeled_pairs:
            reasons.append("no readable L/R markers")
        elif label_coverage < 0.5:
            reasons.append("L/R markers cover too few frames")
        elif label_consistency < 0.85:
            reasons.append("L/R markers disagree across frames")
        anomalies.span(
            "eye_identity_unconfirmed",
            WARNING,
            "both",
            0,
            "; ".join(reasons) + ". A human must confirm the eye order.",
            details={
                "labeled_frames": labeled_pairs,
                "total_frames": total,
                "label_coverage": round(label_coverage, 4),
            },
        )
        anomalies.extend_last(last)
    if (
        labels_confirmed
        and metadata_order
        and metadata_order != dominant_order
    ):
        anomalies.span(
            "metadata_order_conflict",
            WARNING,
            "both",
            0,
            "Container eye-order metadata conflicts with the burned-in eye markers.",
            details={"metadata_order": metadata_order, "marker_order": dominant_order},
        )
        anomalies.extend_last(last)

    clock_frames = sum(
        1
        for frame in frames
        if frame.first.source_frame is not None and frame.second.source_frame is not None
    )
    if clock_frames < max(1, math.ceil(total * 0.5)):
        anomalies.span(
            "frame_clock_unconfirmed",
            WARNING,
            "both",
            0,
            "Frame-number markers are absent or unreadable; timing gaps cannot be fully verified.",
            details={"readable_frames": clock_frames, "total_frames": total},
        )
        anomalies.extend_last(last)

    anomalies.items.sort(
        key=lambda item: (
            item["start_frame"],
            0 if item["severity"] == CRITICAL else 1,
            item["type"],
        )
    )
    critical_count = sum(item["severity"] == CRITICAL for item in anomalies.items)
    warning_count = sum(item["severity"] == WARNING for item in anomalies.items)
    status = "fail" if critical_count else ("warning" if warning_count else "pass")

    identity = {
        "first_slot": "A",
        "second_slot": "B",
        "detected_order": dominant_order,
        "selected_order": chosen_order,
        "confirmed": chosen_order is not None,
        "source": identity_source,
        "labeled_frames": labeled_pairs,
        "unknown_frames": identity_unknown_frames,
        "label_coverage": round(label_coverage, 4),
        "label_consistency": round(label_consistency, 4),
        "manual_review_required": chosen_order is None,
    }
    return {
        "schema_version": 1,
        "input": {"path": str(media.path), "name": media.path.name},
        "media": {
            "width": media.width,
            "height": media.height,
            "eye_width": _eye_dimensions_from_info(media, options.packing)[0],
            "eye_height": _eye_dimensions_from_info(media, options.packing)[1],
            "packing": options.packing,
            "frame_rate": media.frame_rate_text,
            "frame_rate_decimal": float(media.frame_rate),
            "frame_count": total,
            "duration_seconds": round(float(total / media.frame_rate), 6),
            "codec": media.codec,
            "pixel_format": media.pixel_format,
            "has_audio": media.has_audio,
        },
        "options": {
            "max_disparity_px": disparity_limit,
            "max_disparity_ratio": options.max_disparity_ratio,
            "max_vertical_px": options.max_vertical_px,
            "assumed_order": options.assumed_order,
        },
        "eye_identity": identity,
        "summary": {
            "status": status,
            "critical_count": critical_count,
            "warning_count": warning_count,
            "frames_analyzed": total,
            "frames_with_measurement": measured_frames,
            "frames_with_frame_clock": clock_frames,
            "manual_review_required": identity["manual_review_required"],
        },
        "anomalies": anomalies.items,
        "frames": [frame.to_dict() for frame in frames],
        "frame_index_range": [0, last],
    }
