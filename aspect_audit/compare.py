from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .config import AuditConfig
from .models import Mismatch


STABLE_LAYOUTS = {"full", "letterbox", "pillarbox", "fourside"}
BAR_LAYOUTS = {"letterbox", "pillarbox", "fourside"}


def parse_aspect(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower().replace(" ", "")
    if ":" in text:
        left, right = text.split(":", 1)
        denominator = float(right)
        return float(left) / denominator if denominator else None
    return float(text)


@dataclass
class ScheduleEntry:
    start_frame: int
    end_frame: int
    aspect: float
    label: str
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "start_frame": self.start_frame,
            "end_frame": self.end_frame,
            "aspect": round(self.aspect, 6),
            "label": self.label,
            "note": self.note,
        }


def _row_bounds(row: dict, fps: float, frame_count: int) -> tuple[int, int]:
    if "start_frame" in row and row["start_frame"] not in (None, ""):
        start = int(round(float(row["start_frame"])))
    elif "start_sec" in row and row["start_sec"] not in (None, ""):
        start = int(round(float(row["start_sec"]) * fps))
    elif "start_time" in row:
        start = int(round(float(row["start_time"]) * fps))
    else:
        raise ValueError("schedule row needs start_frame or start_sec")
    if "end_frame" in row and row["end_frame"] not in (None, ""):
        end = int(round(float(row["end_frame"])))
    elif "end_sec" in row and row["end_sec"] not in (None, ""):
        end = int(round(float(row["end_sec"]) * fps)) - 1
    elif "end_time" in row and row["end_time"] not in (None, ""):
        end = int(round(float(row["end_time"]) * fps)) - 1
    else:
        end = frame_count - 1
    return max(0, start), min(frame_count - 1, end)


def load_schedule(
    path: str | Path, fps: float, frame_count: int
) -> tuple[list[ScheduleEntry], dict]:
    source = Path(path)
    if source.suffix.lower() == ".json":
        payload = json.loads(source.read_text(encoding="utf-8"))
        rows = payload.get("entries", payload) if isinstance(payload, dict) else payload
        metadata = {
            key: value for key, value in (
                payload.items() if isinstance(payload, dict) else []
            ) if key != "entries"
        }
    else:
        with source.open(newline="", encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))
        metadata = {}
    entries: list[ScheduleEntry] = []
    for row in rows:
        start, end = _row_bounds(row, fps, frame_count)
        if end < start:
            continue
        raw_aspect = row.get("aspect", row.get("aspect_ratio"))
        aspect = parse_aspect(raw_aspect)
        if aspect is None:
            continue
        entries.append(
            ScheduleEntry(
                start_frame=start,
                end_frame=end,
                aspect=aspect,
                label=str(row.get("label") or raw_aspect),
                note=str(row.get("note") or ""),
            )
        )
    entries.sort(key=lambda item: item.start_frame)
    return entries, metadata


def compare_schedule(
    frames: list,
    entries: list[ScheduleEntry],
    cfg: AuditConfig,
) -> list[Mismatch]:
    mismatches: list[Mismatch] = []
    start: int | None = None
    reason: str | None = None
    detected_values: list[float] = []
    declared_values: list[float] = []

    def flush(end_index: int) -> None:
        nonlocal start, reason, detected_values, declared_values
        if start is None or end_index < start or reason is None:
            return
        mismatches.append(
            Mismatch(
                start_frame=start,
                end_frame=end_index,
                start_time=frames[start].time,
                end_time=frames[end_index].time,
                detected_aspect=float(np.median(detected_values)),
                declared_aspect=float(np.median(declared_values)),
                reason=reason,
            )
        )
        start = None
        reason = None
        detected_values = []
        declared_values = []

    for index, frame in enumerate(frames):
        entry = next(
            (
                item for item in entries
                if item.start_frame <= index <= item.end_frame
            ),
            None,
        )
        if frame.layout not in STABLE_LAYOUTS or entry is None:
            flush(index - 1)
            continue
        difference = abs(frame.aspect - entry.aspect)
        if difference <= cfg.aspect_tolerance:
            flush(index - 1)
            continue
        current_reason = (
            f"detected {frame.aspect:.3f}:1 vs declared "
            f"{entry.aspect:.3f}:1 (delta {difference:.3f})"
        )
        if start is not None and current_reason != reason:
            flush(index - 1)
        if start is None:
            start = index
            reason = current_reason
        detected_values.append(frame.aspect)
        declared_values.append(entry.aspect)
    flush(len(frames) - 1)
    return mismatches


def _bar_family(layout: str) -> str:
    if layout == "letterbox":
        return "horizontal"
    if layout == "pillarbox":
        return "vertical"
    if layout == "fourside":
        return "four-side"
    return "none"


def _expected_truth_map(truth: dict, frame_count: int) -> tuple[list[str], list]:
    expected = ["unknown"] * frame_count
    expected_aspect: list[float | None] = [None] * frame_count
    for segment in truth.get("segments", []):
        start = max(0, int(segment["start_frame"]))
        end = min(frame_count - 1, int(segment["end_frame"]))
        aspect = segment.get("aspect")
        for index in range(start, end + 1):
            expected[index] = str(segment.get("layout", "unknown"))
            expected_aspect[index] = float(aspect) if aspect is not None else None
    return expected, expected_aspect


def _group_interval_frames(
    frames: list,
    indices: list[int],
    reason: str,
) -> list[dict]:
    intervals: list[dict] = []
    start: int | None = None
    previous: int | None = None
    for index in indices:
        if start is None:
            start = index
        elif previous is not None and index != previous + 1:
            intervals.append(
                _interval_dict(frames, start, previous, reason)
            )
            start = index
        previous = index
    if start is not None and previous is not None:
        intervals.append(_interval_dict(frames, start, previous, reason))
    return intervals


def _interval_dict(frames: list, start: int, end: int, reason: str) -> dict:
    return {
        "start_frame": start,
        "end_frame": end,
        "start_time": round(frames[start].time, 4),
        "end_time": round(frames[end].time, 4),
        "frames": end - start + 1,
        "reason": reason,
    }


def _switch_checks(
    analysis: dict, truth: dict, fps: float
) -> dict:
    detected = analysis["switches"]
    truth_frames = [int(value) for value in truth.get("switch_frames", [])]
    used: set[int] = set()
    checks: list[dict] = []
    max_match = max(6, int(round(fps * 2.0)))
    for truth_frame in truth_frames:
        candidates = [
            (abs(int(item["frame"]) - truth_frame), index, item)
            for index, item in enumerate(detected) if index not in used
        ]
        candidates.sort(key=lambda item: item[0])
        if candidates and candidates[0][0] <= max_match:
            _, index, item = candidates[0]
            used.add(index)
            detected_frame = int(item["frame"])
            checks.append(
                {
                    "truth_frame": truth_frame,
                    "detected_frame": detected_frame,
                    "error_frames": detected_frame - truth_frame,
                    "error_seconds": round((detected_frame - truth_frame) / fps, 4),
                    "matched": True,
                    "confidence": item.get("confidence"),
                }
            )
        else:
            checks.append(
                {
                    "truth_frame": truth_frame,
                    "detected_frame": None,
                    "error_frames": None,
                    "error_seconds": None,
                    "matched": False,
                    "confidence": None,
                }
            )
    false_positive_switches = [
        {
            "frame": int(item["frame"]),
            "time": item["time"],
            "from_layout": item["from_layout"],
            "to_layout": item["to_layout"],
            "confidence": item["confidence"],
        }
        for index, item in enumerate(detected) if index not in used
    ]
    return {
        "switch_checks": checks,
        "false_positive_switches": false_positive_switches,
    }


def _truth_false_positive_intervals(
    frames: list,
    truth: dict,
    boundary_guard: int = 2,
) -> tuple[list[dict], list[dict]]:
    expected, expected_aspect = _expected_truth_map(truth, len(frames))
    boundaries = {
        int(segment["start_frame"])
        for segment in truth.get("segments", [])
    } | {
        int(segment["end_frame"]) + 1
        for segment in truth.get("segments", [])
    }
    false_positive_indices: list[int] = []
    aspect_deviation_indices: list[int] = []
    for index, frame in enumerate(frames):
        truth_layout = expected[index]
        if truth_layout == "unknown":
            continue
        if any(abs(index - boundary) <= boundary_guard for boundary in boundaries):
            continue
        truth_family = _bar_family(truth_layout)
        detected_family = _bar_family(frame.layout)
        if truth_family == "none" and detected_family != "none":
            false_positive_indices.append(index)
        elif truth_family != "none" and detected_family not in ("none", truth_family):
            false_positive_indices.append(index)
        if (
            frame.layout in STABLE_LAYOUTS
            and expected_aspect[index] is not None
            and abs(frame.aspect - expected_aspect[index]) > 0.07
        ):
            aspect_deviation_indices.append(index)
    return (
        _group_interval_frames(
            frames, false_positive_indices,
            "detected matte type conflicts with truth outside switch boundary",
        ),
        _group_interval_frames(
            frames, aspect_deviation_indices,
            "detected active aspect deviates from truth by more than 0.07",
        ),
    )


def _event_bounds(payload: Any) -> tuple[int, int, str | None] | None:
    if isinstance(payload, list):
        if not payload:
            return None
        payload = payload[0]
    if not isinstance(payload, dict):
        return None
    if "start_frame" not in payload or "end_frame" not in payload:
        return None
    return (
        int(payload["start_frame"]),
        int(payload["end_frame"]),
        payload.get("edge"),
    )


def _best_event_match(expected: tuple[int, int, str | None], events: list) -> dict:
    start, end, edge = expected
    best = None
    best_overlap = -1
    for event in events:
        if edge and event.edge and edge != event.edge:
            continue
        overlap = max(0, min(end, event.end_frame) - max(start, event.start_frame) + 1)
        if overlap > best_overlap:
            best = event
            best_overlap = overlap
    expected_frames = end - start + 1
    if best is None:
        return {
            "truth_start_frame": start,
            "truth_end_frame": end,
            "detected_start_frame": None,
            "detected_end_frame": None,
            "overlap_frames": 0,
            "recall": 0.0,
            "confidence": None,
        }
    return {
        "truth_start_frame": start,
        "truth_end_frame": end,
        "detected_start_frame": best.start_frame,
        "detected_end_frame": best.end_frame,
        "overlap_frames": best_overlap,
        "recall": round(best_overlap / max(1, expected_frames), 4),
        "confidence": round(best.confidence, 4),
        "edge": best.edge,
    }


def _event_checks(analysis: dict, truth: dict) -> dict:
    truth_events = truth.get("events", {})
    detected = analysis["events"]
    mapping = {
        "full_black": detected["black"],
        "subtitle_in_black_bar": detected["subtitle"],
        "fade_in": detected["fade"],
    }
    result: dict = {}
    for kind, events in mapping.items():
        bounds = _event_bounds(truth_events.get(kind))
        result[kind] = _best_event_match(bounds, events) if bounds else None
    return result


def compare_all(
    analysis: dict,
    cfg: AuditConfig | None = None,
    schedule_path: str | Path | None = None,
    truth_path: str | Path | None = None,
) -> dict:
    cfg = cfg or AuditConfig()
    frames = analysis["frames"]
    fps = float(analysis["video"]["fps"])
    result: dict = {
        "declared_schedule": None,
        "mismatches": [],
        "truth_validation": None,
    }
    if schedule_path is not None:
        entries, metadata = load_schedule(
            schedule_path, fps, len(frames)
        )
        mismatches = compare_schedule(frames, entries, cfg)
        result["declared_schedule"] = {
            "path": str(schedule_path),
            "metadata": metadata,
            "entries": [entry.as_dict() for entry in entries],
        }
        result["mismatches"] = [item.as_dict() for item in mismatches]
    if truth_path is not None:
        truth = json.loads(Path(truth_path).read_text(encoding="utf-8"))
        switch_data = _switch_checks(analysis, truth, fps)
        false_positive_intervals, aspect_deviations = (
            _truth_false_positive_intervals(frames, truth)
        )
        result["truth_validation"] = {
            "path": str(truth_path),
            "truth_switch_frames": [
                int(value) for value in truth.get("switch_frames", [])
            ],
            **switch_data,
            "false_positive_intervals": false_positive_intervals,
            "aspect_deviation_intervals": aspect_deviations,
            "event_checks": _event_checks(analysis, truth),
        }
    return result
