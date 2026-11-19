from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable

import cv2
import numpy as np

from .config import AuditConfig
from .media import ffprobe_video, iter_rgb_frames
from .models import Event, FrameResult, Rect, Segment


CANONICAL_RATIOS = {
    "16:9": 16 / 9,
    "1.85:1": 1.85,
    "2.00:1": 2.0,
    "2.35:1": 2.35,
    "2.39:1": 2.39,
    "4:3": 4 / 3,
    "1:1": 1.0,
    "9:16": 9 / 16,
}


def canonical_aspect(aspect: float, tolerance: float = 0.035) -> str:
    name, value = min(CANONICAL_RATIOS.items(), key=lambda item: abs(item[1] - aspect))
    return name if abs(value - aspect) <= tolerance else f"{aspect:.3f}:1"


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _median_filter_1d(values: np.ndarray, size: int = 5) -> np.ndarray:
    if values.size < size:
        return values.copy()
    pad = size // 2
    padded = np.pad(values, pad, mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(padded, size)
    return np.median(windows, axis=1)


def _leading_true(profile: np.ndarray, threshold: float) -> int:
    count = 0
    for value in profile:
        if value < threshold:
            break
        count += 1
    return count


def _trailing_true(profile: np.ndarray, threshold: float) -> int:
    count = 0
    for value in profile[::-1]:
        if value < threshold:
            break
        count += 1
    return count


def _edge_depth(
    profile: np.ndarray,
    threshold: float,
    min_bar: int,
    max_gap: int,
) -> int:
    """Find a matte depth while bridging short bright runs such as subtitles."""
    depth = 0
    for index, value in enumerate(profile):
        if value >= threshold:
            depth = index + 1
            continue
        if depth < min_bar:
            break
        lookahead = profile[index : index + max_gap + 1]
        if lookahead.size and float(np.max(lookahead)) >= threshold:
            continue
        break
    return depth if depth >= min_bar else 0


def _edge_contrast(
    gray: np.ndarray, side: str, depth: int, band: int = 7
) -> float:
    height, width = gray.shape
    if side == "top":
        if depth <= 0 or depth + band >= height:
            return 0.0
        outer = gray[max(0, depth - band) : depth]
        inner = gray[depth : min(height, depth + band)]
    elif side == "bottom":
        if depth <= 0 or depth + band >= height:
            return 0.0
        outer = gray[height - depth : height - max(0, depth - band)]
        inner = gray[height - depth - band : height - depth]
    elif side == "left":
        if depth <= 0 or depth + band >= width:
            return 0.0
        outer = gray[:, max(0, depth - band) : depth]
        inner = gray[:, depth : min(width, depth + band)]
    else:
        if depth <= 0 or depth + band >= width:
            return 0.0
        outer = gray[:, width - depth : width - max(0, depth - band)]
        inner = gray[:, width - depth - band : width - depth]
    return float(
        np.clip(np.mean(inner, axis=(0, 1)) - np.mean(outer, axis=(0, 1)), 0, None)
    )


def _subtitle_evidence(
    gray: np.ndarray,
    side: str,
    depth: int,
    cfg: AuditConfig,
) -> tuple[float, int]:
    if depth < 8:
        return 0.0, 0
    height, width = gray.shape
    inset = 2
    if side in ("top", "bottom"):
        if side == "top":
            roi = gray[inset:depth, inset : width - inset]
        else:
            roi = gray[height - depth : height - inset, inset : width - inset]
    else:
        if side == "left":
            roi = gray[inset : height - inset, inset:depth]
        else:
            roi = gray[inset : height - inset, width - depth : width - inset]
    if roi.size == 0:
        return 0.0, 0
    background = float(np.percentile(roi, 60))
    bright = roi > max(58.0, background + cfg.subtitle_min_luma_delta)
    bright_fraction = float(np.mean(bright))
    count, _, stats, _ = cv2.connectedComponentsWithStats(
        bright.astype(np.uint8), connectivity=8
    )
    components = int(np.sum(stats[1:, cv2.CC_STAT_AREA] >= 3)) if count > 1 else 0
    if (
        bright_fraction < cfg.subtitle_min_bright_fraction
        or components < 4
        or bright_fraction > cfg.subtitle_max_band_fraction
    ):
        return 0.0, components
    confidence = _clamp(
        0.45 + 8.0 * bright_fraction + min(components, 20) / 80.0, 0.0, 0.99
    )
    return confidence, components


@dataclass
class _RawFrame:
    layout: str
    rect: Rect
    bars: dict[str, int]
    edge_contrast: dict[str, float]
    brightness: float
    scene_brightness: float
    dark_ratio: float
    confidence: float
    subtitle_edges: dict[str, float]
    note: str = ""


def _raw_detect(gray: np.ndarray, cfg: AuditConfig) -> _RawFrame:
    height, width = gray.shape
    brightness = float(np.mean(gray))
    dark_ratio = float(np.mean(gray <= cfg.dark_level))
    row_profile = _median_filter_1d(
        np.mean(gray <= cfg.dark_level, axis=1).astype(np.float64)
    )
    col_profile = _median_filter_1d(
        np.mean(gray <= cfg.dark_level, axis=0).astype(np.float64)
    )
    threshold = 0.90
    min_bar = cfg.min_bar_px
    max_gap = max(6, int(round(height * 0.06)))
    top = _edge_depth(row_profile, threshold, min_bar, max_gap)
    bottom = _edge_depth(row_profile[::-1], threshold, min_bar, max_gap)
    left = _edge_depth(col_profile, threshold, min_bar, max_gap)
    right = _edge_depth(col_profile[::-1], threshold, min_bar, max_gap)

    contrasts = {
        "top": _edge_contrast(gray, "top", top),
        "bottom": _edge_contrast(gray, "bottom", bottom),
        "left": _edge_contrast(gray, "left", left),
        "right": _edge_contrast(gray, "right", right),
    }
    bars = {"top": top, "bottom": bottom, "left": left, "right": right}
    rect = Rect(left, top, width - left - right, height - top - bottom)
    if rect.width > 1 and rect.height > 1:
        scene_brightness = float(
            np.mean(gray[rect.y : rect.y + rect.height, rect.x : rect.x + rect.width])
        )
    else:
        scene_brightness = brightness
    if brightness <= cfg.black_mean and (
        dark_ratio >= cfg.black_dark_ratio or brightness < 3.0
    ):
        layout = "black"
        confidence = _clamp(
            0.65 + 0.3 * dark_ratio + 0.05 * (1.0 - brightness / 10.0), 0, 0.99
        )
        return _RawFrame(
            layout, Rect(0, 0, width, height), bars, contrasts,
            brightness, scene_brightness, dark_ratio, confidence, {},
            "whole frame is black"
        )

    active_area = rect.width * rect.height / max(1, width * height)
    strong_sides = [side for side, value in contrasts.items() if value >= 7.0]
    if (
        top >= min_bar and bottom >= min_bar
        and left >= min_bar and right >= min_bar
        and len(strong_sides) >= 3
    ):
        layout = "fourside"
    elif top >= min_bar and bottom >= min_bar and (
        "top" in strong_sides or "bottom" in strong_sides
    ):
        layout = "letterbox"
    elif left >= min_bar and right >= min_bar and (
        "left" in strong_sides or "right" in strong_sides
    ):
        layout = "pillarbox"
    elif not (top or bottom or left or right):
        layout = "full"
    else:
        layout = "unknown"

    strong_values = [contrasts[side] for side in strong_sides]
    mean_contrast = float(np.mean(strong_values)) if strong_values else 0.0
    coverage = 0.0
    present = [side for side in strong_sides if bars[side] > 0]
    if present:
        if layout == "letterbox":
            present = ["top", "bottom"]
            coverage = float(np.mean([row_profile[:top].mean(), row_profile[-bottom:].mean()]))
        elif layout == "pillarbox":
            present = ["left", "right"]
            coverage = float(np.mean([col_profile[:left].mean(), col_profile[-right:].mean()]))
        else:
            coverage = float(min(dark_ratio, 0.98))
    else:
        coverage = 1.0 - dark_ratio
    temporal_hint = 1.0
    if layout == "full":
        confidence = _clamp(
            0.62 + 0.32 * _clamp(1.0 - dark_ratio, 0, 1), 0.0, 0.97
        )
    else:
        confidence = _clamp(
            0.48 * _clamp(mean_contrast / 35.0, 0, 1)
            + 0.42 * _clamp(coverage, 0, 1)
            + 0.10 * temporal_hint,
            0.0,
            0.99,
        )
    if active_area < 0.18:
        layout = "unknown"
        confidence = min(confidence, 0.25)
    if scene_brightness < cfg.fade_peak:
        layout = "unknown"
        confidence = min(confidence, 0.42)
    subtitle_edges: dict[str, float] = {}
    for side in ("top", "bottom", "left", "right"):
        value, _ = _subtitle_evidence(gray, side, bars[side], cfg)
        if value:
            subtitle_edges[side] = value
    note = ""
    if layout == "unknown" and scene_brightness < cfg.fade_peak:
        note = "low-light frame excluded from layout voting"
    return _RawFrame(
        layout, rect, bars, contrasts, brightness, scene_brightness, dark_ratio,
        confidence, subtitle_edges, note
    )


@dataclass
class _StableState:
    layout: str
    bars: np.ndarray
    count: int


def _same_layout(
    layout: str, bars: np.ndarray, state: _StableState, tolerance: float
) -> bool:
    return layout == state.layout and bool(
        np.all(np.abs(bars - state.bars) <= tolerance)
    )


def _median_bars(samples: Iterable[np.ndarray]) -> np.ndarray:
    rows = list(samples)
    return np.median(np.stack(rows), axis=0)


def _result_from_state(
    index: int,
    time: float,
    raw: _RawFrame,
    state: _StableState,
    native_width: int,
    native_height: int,
    analysis_width: int,
    analysis_height: int,
    stable_frames: int,
) -> FrameResult:
    sx = native_width / analysis_width
    sy = native_height / analysis_height
    t, b, l, r = state.bars
    native_bars = {
        "top": int(round(t * sy)),
        "bottom": int(round(b * sy)),
        "left": int(round(l * sx)),
        "right": int(round(r * sx)),
    }
    rect = Rect(
        native_bars["left"],
        native_bars["top"],
        native_width - native_bars["left"] - native_bars["right"],
        native_height - native_bars["top"] - native_bars["bottom"],
    )
    if state.layout == "full":
        bar_types: list[str] = []
    elif state.layout == "letterbox":
        bar_types = ["top", "bottom"]
    elif state.layout == "pillarbox":
        bar_types = ["left", "right"]
    elif state.layout == "fourside":
        bar_types = ["top", "bottom", "left", "right"]
    else:
        bar_types = []
    temporal = min(stable_frames, 6) / 6.0
    confidence = _clamp(raw.confidence * 0.68 + 0.22 * temporal + 0.10, 0, 0.99)
    return FrameResult(
        frame=index,
        time=time,
        layout=state.layout,
        rect=rect,
        aspect=rect.aspect,
        confidence=confidence,
        brightness=raw.brightness,
        scene_brightness=raw.scene_brightness,
        dark_ratio=raw.dark_ratio,
        bars=native_bars,
        bar_types=bar_types,
        edge_contrast={
            side: round(value, 3) for side, value in raw.edge_contrast.items()
        },
        subtitle_edges=sorted(raw.subtitle_edges),
        subtitle_confidence=max(raw.subtitle_edges.values(), default=0.0),
        note=raw.note,
    )


def _transition_result(
    index: int,
    time: float,
    raw: _RawFrame,
    native_width: int,
    native_height: int,
) -> FrameResult:
    if raw.layout == "black":
        rect = Rect(0, 0, native_width, native_height)
        bar_types = []
    else:
        rect = Rect(0, 0, native_width, native_height)
        bar_types = []
    return FrameResult(
        frame=index,
        time=time,
        layout=raw.layout,
        rect=rect,
        aspect=rect.aspect,
        confidence=raw.confidence,
        brightness=raw.brightness,
        scene_brightness=raw.scene_brightness,
        dark_ratio=raw.dark_ratio,
        bars={key: 0 for key in raw.bars},
        bar_types=bar_types,
        edge_contrast={
            side: round(value, 3) for side, value in raw.edge_contrast.items()
        },
        subtitle_edges=sorted(raw.subtitle_edges),
        subtitle_confidence=max(raw.subtitle_edges.values(), default=0.0),
        note=raw.note,
    )


def _temporally_stabilize(
    raws: list[_RawFrame],
    fps: float,
    native_width: int,
    native_height: int,
    analysis_width: int,
    analysis_height: int,
    cfg: AuditConfig,
) -> list[FrameResult]:
    sx = analysis_width / native_width
    sy = analysis_height / native_height
    tolerance = cfg.boundary_tolerance_px / max((sx + sy) / 2, 0.01)
    results: list[FrameResult | None] = [None] * len(raws)
    state: _StableState | None = None
    recent: list[np.ndarray] = []
    pending: list[tuple[int, str, np.ndarray]] = []
    for index, raw in enumerate(raws):
        time = index / fps if fps else 0.0
        vector = np.array(
            [raw.bars["top"], raw.bars["bottom"], raw.bars["left"], raw.bars["right"]],
            dtype=np.float64,
        )
        if raw.layout in ("black", "unknown"):
            if raw.layout == "black":
                pending.clear()
            results[index] = _transition_result(
                index, time, raw, native_width, native_height
            )
            continue
        if state is None:
            pending.append((index, raw.layout, vector))
            if len(pending) < cfg.min_layout_frames:
                results[index] = _transition_result(
                    index, time, raw, native_width, native_height
                )
                continue
            state = _StableState(
                pending[0][1], _median_bars(item[2] for item in pending), len(pending)
            )
            start = pending[0][0]
            for pending_index, _, pending_vector in pending:
                pending_raw = raws[pending_index]
                provisional = replace(state, bars=pending_vector)
                results[pending_index] = _result_from_state(
                    pending_index, pending_index / fps, pending_raw, provisional,
                    native_width, native_height, analysis_width, analysis_height,
                    len(pending),
                )
            pending.clear()
            recent = [state.bars.copy()]
            continue
        if _same_layout(raw.layout, vector, state, tolerance):
            pending.clear()
            recent.append(vector)
            recent = recent[-7:]
            state = _StableState(raw.layout, _median_bars(recent), state.count + 1)
            results[index] = _result_from_state(
                index, time, raw, state, native_width, native_height,
                analysis_width, analysis_height, state.count,
            )
            continue
        if not pending:
            pending.append((index, raw.layout, vector))
        elif pending[-1][1] == raw.layout and bool(
            np.all(np.abs(vector - pending[-1][2]) <= tolerance * 1.5)
        ):
            pending.append((index, raw.layout, vector))
        else:
            pending = [(index, raw.layout, vector)]
        results[index] = _result_from_state(
            index, time, raw, state, native_width, native_height,
            analysis_width, analysis_height, state.count,
        )
        if len(pending) >= cfg.min_layout_frames:
            new_layout = pending[0][1]
            new_bars = _median_bars(item[2] for item in pending)
            start = pending[0][0]
            state = _StableState(new_layout, new_bars, len(pending))
            recent = [new_bars.copy()]
            for pending_index, _, pending_vector in pending:
                pending_raw = raws[pending_index]
                results[pending_index] = _result_from_state(
                    pending_index, pending_index / fps, pending_raw,
                    replace(state, bars=pending_vector), native_width, native_height,
                    analysis_width, analysis_height, len(pending),
                )
            pending.clear()
    return [
        item if item is not None else _transition_result(
            index, index / fps if fps else 0.0, raws[index],
            native_width, native_height,
        )
        for index, item in enumerate(results)
    ]


def _find_fade_events(
    frames: list[FrameResult], fps: float, cfg: AuditConfig
) -> list[Event]:
    if len(frames) < cfg.fade_min_frames:
        return []
    brightness = np.array([frame.scene_brightness for frame in frames])
    events: list[Event] = []
    index = 0
    max_span = max(cfg.fade_min_frames, int(round(fps * 3.0)))
    while index < len(frames):
        if brightness[index] >= cfg.fade_peak:
            index += 1
            continue
        end = index
        while end < len(frames) and end - index < max_span:
            if brightness[end] >= cfg.fade_peak:
                break
            end += 1
        if end >= len(frames) or end - index < cfg.fade_min_frames:
            index = max(index + 1, end)
            continue
        values = brightness[index:end]
        diffs = np.diff(values)
        positive = float(np.mean(diffs >= -0.5)) if diffs.size else 0.0
        rise = float(values[-1] - values[0])
        max_step = float(np.max(np.abs(diffs))) if diffs.size else 999.0
        if positive < 0.65 or rise < 28.0 or max_step > 42.0:
            index += 1
            continue
        confidence = _clamp(
            0.40 + 0.28 * positive + 0.22 * _clamp(rise / 100.0, 0, 1)
            + 0.10 * (1.0 - _clamp(max_step / 50.0, 0, 1)),
            0,
            0.98,
        )
        event = Event(
            kind="fade_in",
            start_frame=index,
            end_frame=end - 1,
            start_time=index / fps if fps else 0.0,
            end_time=(end - 1) / fps if fps else 0.0,
            confidence=confidence,
            detail=(
                f"luma {values[0]:.1f}->{values[-1]:.1f}; "
                f"{end - index} frames; gradual rise"
            ),
        )
        events.append(event)
        for frame_index in range(index, end):
            frame = frames[frame_index]
            if frame.layout != "black":
                frame.layout = "fade"
                frame.confidence = min(0.60, confidence)
                frame.note = "fade-in: excluded from aspect voting"
        index = max(end, index + 1)
    return events


def _group_events(
    frames: list[FrameResult],
    predicate,
    kind: str,
    fps: float,
    detail: str,
) -> list[Event]:
    events: list[Event] = []
    start: int | None = None
    values: list[float] = []
    for index, frame in enumerate(frames + [None]):
        matched = frame is not None and predicate(frame)
        if matched:
            if start is None:
                start = index
            values.append(frame.confidence)
        elif start is not None:
            f0 = frames[start]
            f1 = frames[index - 1]
            events.append(
                Event(
                    kind=kind,
                    start_frame=start,
                    end_frame=index - 1,
                    start_time=f0.time,
                    end_time=f1.time,
                    confidence=float(np.mean(values)),
                    detail=f"{index - start} frames; {detail}",
                )
            )
            start = None
            values = []
    return events


def _black_events(
    frames: list[FrameResult], fps: float, fade_events: list[Event]
) -> list[Event]:
    events = _group_events(
        frames, lambda frame: frame.layout == "black", "full_black", fps,
        "whole-frame black",
    )
    for event in events:
        frame_mean = float(
            np.mean([frames[i].brightness for i in range(
                event.start_frame, event.end_frame + 1
            )])
        )
        event.detail += f"; mean luma {frame_mean:.2f}"
    return [
        event for event in events
        if event.end_frame > event.start_frame
        and not any(
            fade.start_frame <= event.start_frame
            and event.end_frame <= fade.end_frame
            for fade in fade_events
        )
    ]


def _subtitle_events(frames: list[FrameResult], fps: float) -> list[Event]:
    events: list[Event] = []
    for edge in ("top", "bottom", "left", "right"):
        edge_events = _group_events(
            frames,
            lambda frame, side=edge: side in frame.subtitle_edges,
            "subtitle_in_black_bar",
            fps,
            f"bright subtitle-like detail inside {edge} bar",
        )
        for event in edge_events:
            event.edge = edge
            event.confidence = float(
                np.mean([
                    frames[i].subtitle_confidence
                    for i in range(event.start_frame, event.end_frame + 1)
                ])
            )
        events.extend(edge_events)
    return sorted(events, key=lambda event: (event.start_frame, event.edge or ""))


def _make_segments(
    frames: list[FrameResult], cfg: AuditConfig
) -> list[Segment]:
    allowed = {"full", "letterbox", "pillarbox", "fourside"}
    segments: list[Segment] = []
    start: int | None = None
    for index, frame in enumerate(frames + [None]):
        is_stable = frame is not None and frame.layout in allowed
        same = False
        if start is not None and is_stable:
            first = frames[start]
            same = (
                frame.layout == first.layout
                and abs(frame.aspect - first.aspect) <= cfg.aspect_tolerance
                and np.all(
                    np.abs(
                        np.array([frame.bars[key] for key in ("top", "bottom", "left", "right")])
                        - np.array([first.bars[key] for key in ("top", "bottom", "left", "right")])
                    )
                    <= cfg.boundary_tolerance_px * 2
                )
            )
        if start is not None and (not is_stable or not same):
            end = index - 1
            chunk = frames[start:index]
            rects = np.array(
                [[frame.rect.x, frame.rect.y, frame.rect.width, frame.rect.height]
                 for frame in chunk],
                dtype=np.float64,
            )
            x, y, width, height = np.median(rects, axis=0)
            rect = Rect(int(round(x)), int(round(y)), int(round(width)), int(round(height)))
            aspect = float(np.median([frame.aspect for frame in chunk]))
            first = chunk[0]
            bar_types: list[str] = []
            if first.layout == "letterbox":
                bar_types = ["top", "bottom"]
            elif first.layout == "pillarbox":
                bar_types = ["left", "right"]
            elif first.layout == "fourside":
                bar_types = ["top", "bottom", "left", "right"]
            segments.append(
                Segment(
                    layout=first.layout,
                    bar_types=bar_types,
                    aspect=aspect,
                    canonical_aspect=canonical_aspect(aspect, cfg.aspect_tolerance),
                    start_frame=start,
                    end_frame=end,
                    start_time=first.time,
                    end_time=frames[end].time,
                    rect=rect,
                    confidence=float(np.mean([frame.confidence for frame in chunk])),
                )
            )
            start = None
        if is_stable and start is None:
            start = index
    return segments


def _switch_candidates(segments: list[Segment], cfg: AuditConfig) -> list[dict]:
    switches: list[dict] = []
    for previous, current in zip(segments, segments[1:]):
        ratio_changed = abs(previous.aspect - current.aspect) > cfg.aspect_tolerance
        layout_changed = previous.layout != current.layout
        if not (ratio_changed or layout_changed):
            continue
        switches.append(
            {
                "frame": current.start_frame,
                "time": round(current.start_time, 4),
                "from_layout": previous.layout,
                "to_layout": current.layout,
                "from_aspect": round(previous.aspect, 4),
                "to_aspect": round(current.aspect, 4),
                "gap_frames": current.start_frame - previous.end_frame - 1,
                "confidence": round(
                    min(previous.confidence, current.confidence), 4
                ),
            }
        )
    return switches


def detect_frame_layout(
    rgb: np.ndarray, cfg: AuditConfig | None = None
) -> dict:
    """Classify one RGB frame; mainly useful for diagnostics and tests."""
    cfg = cfg or AuditConfig()
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    target_height = max(1, round(gray.shape[0] * cfg.analysis_width / gray.shape[1]))
    analysis = cv2.resize(
        gray, (cfg.analysis_width, target_height), interpolation=cv2.INTER_AREA
    )
    raw = _raw_detect(analysis, cfg)
    return {
        "layout": raw.layout,
        "bars": raw.bars,
        "brightness": raw.brightness,
        "dark_ratio": raw.dark_ratio,
        "confidence": raw.confidence,
        "subtitle_edges": raw.subtitle_edges,
        "note": raw.note,
    }


def analyze_video(
    path: str,
    cfg: AuditConfig | None = None,
) -> dict:
    cfg = cfg or AuditConfig()
    info = ffprobe_video(path)
    width = info["width"]
    height = info["height"]
    fps = info["fps"]
    analysis_height = max(1, round(height * cfg.analysis_width / width))
    raws: list[_RawFrame] = []
    for rgb in iter_rgb_frames(path, width, height):
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        analysis = cv2.resize(
            gray, (cfg.analysis_width, analysis_height),
            interpolation=cv2.INTER_AREA,
        )
        raws.append(_raw_detect(analysis, cfg))
    if not raws:
        raise RuntimeError(f"no video frames decoded from {path}")
    frames = _temporally_stabilize(
        raws, fps, width, height, cfg.analysis_width, analysis_height, cfg
    )
    fade_events = _find_fade_events(frames, fps, cfg)
    black_events = _black_events(frames, fps, fade_events)
    subtitle_events = _subtitle_events(frames, fps)
    segments = _make_segments(frames, cfg)
    switches = _switch_candidates(segments, cfg)
    duration = len(frames) / fps if fps else info.get("duration", 0.0)
    return {
        "video": {
            **info,
            "path": str(path),
            "decoded_frames": len(frames),
            "duration": round(duration, 4),
        },
        "config": cfg.to_dict(),
        "frames": frames,
        "segments": segments,
        "events": {
            "fade": fade_events,
            "black": black_events,
            "subtitle": subtitle_events,
        },
        "switches": switches,
    }
