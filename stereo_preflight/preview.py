from __future__ import annotations

import math
from typing import Any

from .media import MediaInfo, iter_frames, png_bytes, split_eyes


def _black(width: int, height: int) -> bytes:
    return bytes(width * height * 3)


def _gray_copy(data: bytes) -> bytearray:
    output = bytearray(len(data))
    for index in range(0, len(data), 3):
        luma = (data[index] * 54 + data[index + 1] * 183 + data[index + 2] * 19) >> 8
        output[index : index + 3] = bytes((luma, luma, luma))
    return output


def _anaglyph(left: bytes | None, right: bytes | None, width: int, height: int) -> bytes:
    if left is None and right is None:
        return _black(width, height)
    left_gray = _gray_copy(left) if left is not None else bytearray(width * height * 3)
    right_gray = _gray_copy(right) if right is not None else bytearray(width * height * 3)
    output = bytearray(width * height * 3)
    for index in range(0, len(output), 3):
        output[index] = left_gray[index]
        output[index + 1] = right_gray[index]
        output[index + 2] = right_gray[index]
    return bytes(output)


def _side_by_side(left: bytes | None, right: bytes | None, width: int, height: int, gap: int = 8) -> tuple[int, bytes]:
    output_width = width * 2 + gap
    output = bytearray(output_width * height * 3)
    stride = output_width * 3
    source_stride = width * 3
    for y in range(height):
        row = y * stride
        if left is not None:
            source = y * source_stride
            output[row : row + source_stride] = left[source : source + source_stride]
        if right is not None:
            target = row + (width + gap) * 3
            source = y * source_stride
            output[target : target + source_stride] = right[source : source + source_stride]
    return output_width, bytes(output)


def _ring(canvas: bytearray, width: int, height: int, cx: float, cy: float, radius: float, color: tuple[int, int, int]) -> None:
    steps = max(24, round(radius * 6))
    for step in range(steps):
        angle = step * math.tau / steps
        x = round(cx + radius * math.cos(angle))
        y = round(cy + radius * math.sin(angle))
        if 0 <= x < width and 0 <= y < height:
            offset = (y * width + x) * 3
            canvas[offset : offset + 3] = bytes(color)
    for dx, dy in ((1, 0), (0, 1)):
        for sign in (-1, 1):
            for distance in range(3, round(radius) + 6):
                x = round(cx + dx * distance * sign)
                y = round(cy + dy * distance * sign)
                if 0 <= x < width and 0 <= y < height:
                    offset = (y * width + x) * 3
                    canvas[offset : offset + 3] = bytes(color)


def _frame_slot(report: dict[str, Any], source_index: int, slot: str) -> dict[str, Any] | None:
    frames = report.get("frames") or []
    if source_index < 0 or source_index >= len(frames):
        return None
    return frames[source_index].get(slot)


def _source_index(output_index: int, offset: int, total: int) -> int | None:
    source = output_index - offset
    return source if 0 <= source < total else None


def render_preview(
    report: dict[str, Any],
    media: MediaInfo,
    index: int,
    *,
    mode: str = "anaglyph",
    order: str = "ab",
    offset_left: int = 0,
    offset_right: int = 0,
    show_marks: bool = True,
) -> tuple[bytes, dict[str, Any]]:
    if mode not in {"left", "right", "anaglyph", "marked"}:
        raise ValueError("mode must be left, right, anaglyph, or marked")
    if order not in {"ab", "ba"}:
        raise ValueError("order must be ab or ba")
    total = len(report["frames"])
    if index < 0 or index >= total:
        raise IndexError("frame index out of range")

    left_source = _source_index(index, offset_left, total)
    right_source = _source_index(index, offset_right, total)
    wanted = {item for item in (left_source, right_source) if item is not None}
    packed_frames: dict[int, bytes] = {}
    if wanted:
        last = max(wanted)
        for source_index, packed in iter_frames(media):
            if source_index in wanted:
                packed_frames[source_index] = packed
            if source_index >= last:
                break

    eye_w = report["media"]["eye_width"]
    eye_h = report["media"]["eye_height"]
    eyes: dict[str, bytes | None] = {"left": None, "right": None}
    logical_slots = {
        "left": "first" if order == "ab" else "second",
        "right": "second" if order == "ab" else "first",
    }
    for side, source_index in (("left", left_source), ("right", right_source)):
        if source_index is None or source_index not in packed_frames:
            continue
        first, second = split_eyes(packed_frames[source_index], media.width, media.height, report["media"]["packing"])
        eyes[side] = first if logical_slots[side] == "first" else second

    marks: list[dict[str, Any]] = []
    for side, source_index in (("left", left_source), ("right", right_source)):
        if source_index is None:
            continue
        slot = logical_slots[side]
        eye_meta = _frame_slot(report, source_index, slot)
        if not eye_meta:
            continue
        for kind in ("foreground", "background"):
            point = eye_meta.get(kind)
            if point:
                marks.append({"eye": side, "kind": kind, "x": point[0], "y": point[1]})

    if mode == "left":
        image = eyes["left"] or _black(eye_w, eye_h)
        width, height = eye_w, eye_h
        mark_origin = {"left": 0}
    elif mode == "right":
        image = eyes["right"] or _black(eye_w, eye_h)
        width, height = eye_w, eye_h
        mark_origin = {"right": 0}
    elif mode == "anaglyph":
        image = _anaglyph(eyes["left"], eyes["right"], eye_w, eye_h)
        width, height = eye_w, eye_h
        mark_origin = {"left": 0, "right": 0}
    else:
        width, image = _side_by_side(eyes["left"], eyes["right"], eye_w, eye_h)
        height = eye_h
        mark_origin = {"left": 0, "right": eye_w + 8}

    if show_marks:
        canvas = bytearray(image)
        colors = {"left": (255, 210, 50), "right": (70, 230, 255)}
        for mark in marks:
            origin = mark_origin.get(mark["eye"])
            if origin is None:
                continue
            _ring(canvas, width, height, origin + mark["x"], mark["y"], 26, colors[mark["eye"]])
        image = bytes(canvas)

    returns = {
        "index": index,
        "mode": mode,
        "order": order,
        "left_source_frame": left_source,
        "right_source_frame": right_source,
        "marks": marks,
        "width": width,
        "height": height,
    }
    return png_bytes(width, height, image), returns
