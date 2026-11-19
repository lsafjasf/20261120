from __future__ import annotations

import math
from dataclasses import dataclass


RED = (238, 38, 34)
GREEN = (28, 220, 72)
WHITE = (238, 242, 245)
GRID = (44, 51, 60)
SLATE = (10, 13, 18)

LABEL_BOX = (0.055, 0.045, 0.11, 0.17)
FRAME_BOX = (0.36, 0.035, 0.31, 0.17)
FG_BOX = (0.12, 0.29, 0.34, 0.32)
BG_BOX = (0.54, 0.39, 0.34, 0.36)

LETTERS = {
    "L": (
        "10000",
        "10000",
        "10000",
        "10000",
        "10000",
        "10000",
        "11111",
    ),
    "R": (
        "11110",
        "10001",
        "10001",
        "11110",
        "10100",
        "10010",
        "10001",
    ),
}

DIGITS = {
    "0": "ab cdef".replace(" ", ""),
    "1": "bc",
    "2": "abdeg",
    "3": "abcdg",
    "4": "bcfg",
    "5": "acdfg",
    "6": "acdefg",
    "7": "abc",
    "8": "abcdefg",
    "9": "abcdfg",
}
SEGMENT_NAMES = "abcdefg"
SEGMENT_POINTS = {
    "a": (0.50, 0.04),
    "b": (0.91, 0.30),
    "c": (0.91, 0.72),
    "d": (0.50, 0.96),
    "e": (0.09, 0.72),
    "f": (0.09, 0.30),
    "g": (0.50, 0.51),
}


@dataclass(frozen=True)
class EyeSpec:
    label: str
    frame_id: int
    foreground_dx: float = 0.0
    foreground_dy: float = 0.0
    background_dx: float = 0.0
    background_dy: float = 0.0


class Canvas:
    def __init__(self, width: int, height: int, color: tuple[int, int, int] = SLATE):
        self.width = width
        self.height = height
        self.data = bytearray(color * (width * height))

    def pixel(self, x: int, y: int, color: tuple[int, int, int]) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            offset = (y * self.width + x) * 3
            self.data[offset : offset + 3] = bytes(color)

    def rect(self, x: int, y: int, width: int, height: int, color: tuple[int, int, int]) -> None:
        left = max(0, x)
        top = max(0, y)
        right = min(self.width, x + width)
        bottom = min(self.height, y + height)
        if right <= left or bottom <= top:
            return
        row = bytes(color) * (right - left)
        for py in range(top, bottom):
            offset = (py * self.width + left) * 3
            self.data[offset : offset + len(row)] = row

    def disc(self, cx: float, cy: float, radius: float, color: tuple[int, int, int]) -> None:
        r2 = radius * radius
        for y in range(max(0, int(cy - radius)), min(self.height, int(cy + radius) + 1)):
            dy = y + 0.5 - cy
            span = math.sqrt(max(0.0, r2 - dy * dy))
            left = max(0, int(cx - span))
            right = min(self.width, int(cx + span) + 1)
            if right > left:
                offset = (y * self.width + left) * 3
                self.data[offset : offset + (right - left) * 3] = bytes(color) * (right - left)

    def ring(self, cx: float, cy: float, radius: float, thickness: float, color: tuple[int, int, int]) -> None:
        outer = radius
        inner = max(0.0, radius - thickness)
        for y in range(max(0, int(cy - outer)), min(self.height, int(cy + outer) + 1)):
            dy = y + 0.5 - cy
            if abs(dy) > outer:
                continue
            outer_span = math.sqrt(max(0.0, outer * outer - dy * dy))
            if abs(dy) < inner:
                inner_span = math.sqrt(max(0.0, inner * inner - dy * dy))
                self.rect(int(cx - outer_span), y, int((outer_span - inner_span) * 2), 1, color)
                self.rect(int(cx + inner_span), y, int((outer_span - inner_span) + 1), 1, color)
            else:
                self.rect(int(cx - outer_span), y, int(outer_span * 2) + 1, 1, color)

    def line(self, x1: int, y1: int, x2: int, y2: int, color: tuple[int, int, int]) -> None:
        steps = max(abs(x2 - x1), abs(y2 - y1), 1)
        for step in range(steps + 1):
            t = step / steps
            self.pixel(round(x1 + (x2 - x1) * t), round(y1 + (y2 - y1) * t), color)


def _draw_letter(canvas: Canvas, label: str, box: tuple[float, float, float, float]) -> None:
    rows = LETTERS[label]
    x, y, width, height = box
    cell_w = width / 5
    cell_h = height / 7
    gap_x = max(1, int(cell_w * 0.13))
    gap_y = max(1, int(cell_h * 0.13))
    for row, pattern in enumerate(rows):
        for column, value in enumerate(pattern):
            if value == "1":
                left = round(x + column * cell_w) + gap_x
                top = round(y + row * cell_h) + gap_y
                canvas.rect(left, top, max(1, round(cell_w) - gap_x * 2), max(1, round(cell_h) - gap_y * 2), WHITE)


def _draw_digit(canvas: Canvas, digit: str, box: tuple[int, int, int, int]) -> None:
    x, y, width, height = box
    active = DIGITS[digit]
    thickness = max(2, round(min(width, height) * 0.16))
    half_h = max(1, height // 2)
    if "a" in active:
        canvas.rect(x + thickness, y, width - thickness * 2, thickness, WHITE)
    if "b" in active:
        canvas.rect(x + width - thickness, y + thickness, thickness, half_h - thickness, WHITE)
    if "c" in active:
        canvas.rect(x + width - thickness, y + half_h, thickness, height - half_h - thickness, WHITE)
    if "d" in active:
        canvas.rect(x + thickness, y + height - thickness, width - thickness * 2, thickness, WHITE)
    if "e" in active:
        canvas.rect(x, y + half_h, thickness, height - half_h - thickness, WHITE)
    if "f" in active:
        canvas.rect(x, y + thickness, thickness, half_h - thickness, WHITE)
    if "g" in active:
        canvas.rect(x + thickness, y + half_h - thickness // 2, width - thickness * 2, thickness, WHITE)


def _draw_frame_id(canvas: Canvas, frame_id: int, box: tuple[float, float, float, float]) -> None:
    text = f"{frame_id % 1_000_000:06d}"
    x, y, width, height = box
    digit_gap = width * 0.025
    digit_w = (width - digit_gap * 5) / 6
    for index, character in enumerate(text):
        left = round(x + index * (digit_w + digit_gap))
        _draw_digit(canvas, character, (left, round(y), max(1, round(digit_w)), round(height)))


def render_eye(width: int, height: int, spec: EyeSpec) -> bytes:
    canvas = Canvas(width, height)
    grid_step = max(24, round(min(width, height) / 12))
    for x in range(0, width, grid_step):
        canvas.line(x, 0, x, height - 1, GRID)
    for y in range(0, height, grid_step):
        canvas.line(0, y, width - 1, y, GRID)

    label_box = tuple(int(round(v * (width if i % 2 == 0 else height))) for i, v in enumerate(LABEL_BOX))
    frame_box = tuple(int(round(v * (width if i % 2 == 0 else height))) for i, v in enumerate(FRAME_BOX))
    canvas.rect(label_box[0], label_box[1], label_box[2], label_box[3], (0, 0, 0))
    canvas.rect(frame_box[0], frame_box[1], frame_box[2], frame_box[3], (0, 0, 0))
    _draw_letter(canvas, spec.label, label_box)
    _draw_frame_id(canvas, spec.frame_id, frame_box)

    fg_x = width * 0.29 + spec.foreground_dx
    fg_y = height * 0.45 + spec.foreground_dy
    bg_x = width * 0.71 + spec.background_dx
    bg_y = height * 0.57 + spec.background_dy
    fg_radius = max(7.0, width * 0.033)
    bg_radius = max(7.0, width * 0.03)
    canvas.disc(fg_x, fg_y, fg_radius, RED)
    canvas.ring(fg_x, fg_y, fg_radius + 3, 2, WHITE)
    canvas.disc(bg_x, bg_y, bg_radius, GREEN)
    canvas.ring(bg_x, bg_y, bg_radius + 3, 2, WHITE)

    horizon = round(height * 0.70)
    canvas.line(0, horizon, width - 1, horizon, (150, 158, 168))
    canvas.line(round(width * 0.5), 0, round(width * 0.5), height - 1, GRID)
    return bytes(canvas.data)


def _box_pixels(box: tuple[float, float, float, float], width: int, height: int) -> tuple[int, int, int, int]:
    x, y, w, h = box
    return (
        round(x * width),
        round(y * height),
        max(1, round(w * width)),
        max(1, round(h * height)),
    )


def _luma_at(data: bytes, width: int, x: int, y: int) -> int:
    offset = (y * width + x) * 3
    return (data[offset] * 54 + data[offset + 1] * 183 + data[offset + 2] * 19) >> 8


def _box_luma(data: bytes, width: int, height: int, x: int, y: int, radius: int = 1) -> float:
    values = []
    for py in range(max(0, y - radius), min(height, y + radius + 1)):
        for px in range(max(0, x - radius), min(width, x + radius + 1)):
            values.append(_luma_at(data, width, px, py))
    return sum(values) / len(values) if values else 0.0


def decode_label(data: bytes, width: int, height: int) -> tuple[str | None, float]:
    x, y, w, h = _box_pixels(LABEL_BOX, width, height)
    cell_w = w / 5
    cell_h = h / 7
    means = []
    for row in range(7):
        for column in range(5):
            cx = round(x + (column + 0.5) * cell_w)
            cy = round(y + (row + 0.5) * cell_h)
            means.append(_box_luma(data, width, height, cx, cy, max(1, int(min(cell_w, cell_h) * 0.18))))
    scores: dict[str, float] = {}
    for label, rows in LETTERS.items():
        bright = []
        dark = []
        for index, value in enumerate("".join(rows)):
            (bright if value == "1" else dark).append(means[index])
        scores[label] = (sum(bright) / len(bright)) - (sum(dark) / len(dark))
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best, best_score = ranked[0]
    margin = best_score - ranked[1][1]
    if best_score < 22 or margin < 8:
        return None, max(0.0, best_score)
    return best, round(margin, 1)


def decode_frame_id(data: bytes, width: int, height: int) -> tuple[int | None, float]:
    x, y, w, h = _box_pixels(FRAME_BOX, width, height)
    digit_gap = w * 0.025
    digit_w = (w - digit_gap * 5) / 6
    gap_levels = []
    for index in range(5):
        gap_x = round(x + (index + 1) * digit_w + (index + 0.5) * digit_gap)
        for ratio in (0.3, 0.5, 0.7):
            gap_levels.append(_box_luma(data, width, height, gap_x, round(y + ratio * h), 0))
    if not gap_levels or sum(gap_levels) / len(gap_levels) > 82:
        return None, 0.0
    characters = []
    confidence = 100.0
    for index in range(6):
        left = x + index * (digit_w + digit_gap)
        active = set()
        levels = []
        for name in SEGMENT_NAMES:
            nx, ny = SEGMENT_POINTS[name]
            cx = round(left + nx * digit_w)
            cy = round(y + ny * h)
            level = _box_luma(data, width, height, cx, cy, max(1, round(min(digit_w, h) * 0.035)))
            levels.append(level)
            if level >= 105:
                active.add(name)
        best_digit = "?"
        best_distance = 8
        for digit, pattern in DIGITS.items():
            distance = len(active.symmetric_difference(set(pattern)))
            if distance < best_distance:
                best_digit = digit
                best_distance = distance
        if best_distance > 0:
            confidence -= best_distance * 9
        characters.append(best_digit)
    text = "".join(characters)
    if "?" in text or confidence < 55:
        return None, max(0.0, confidence)
    return int(text), max(0.0, confidence)


def detect_marker(data: bytes, width: int, height: int, box: tuple[float, float, float, float], color: str) -> tuple[float, float] | None:
    x, y, w, h = _box_pixels(box, width, height)
    sum_x = 0.0
    sum_y = 0.0
    count = 0
    stride = 1 if width * height < 1_000_000 else 2
    for py in range(y, min(height, y + h), stride):
        offset = (py * width + x) * 3
        for px in range(x, min(width, x + w), stride):
            red = data[offset]
            green = data[offset + 1]
            blue = data[offset + 2]
            matched = (
                red > 130 and red > green * 1.55 and red > blue * 1.35
                if color == "red"
                else green > 105 and green > red * 1.35 and green > blue * 1.12
            )
            if matched:
                sum_x += px
                sum_y += py
                count += 1
            offset += 3 * stride
    minimum = max(12, round(w * h * 0.0003))
    if count < minimum:
        return None
    return sum_x / count, sum_y / count
