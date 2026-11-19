"""Text metrics and line breaking for the deterministic layout engine.

Widths use a tabular model measured in CSS pixels:
- Latin/digit/punctuation glyphs get fixed advance widths per weight class;
- CJK ideographs and fullwidth punctuation occupy 1.0 em;
- wrapping follows CSS white-space: pre-line semantics (soft breaks on spaces
  and CJK boundaries), keeping explicit cue newlines intact.

No font files or browser are required, so results are fully reproducible.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from .parser import CueLine


@dataclass(frozen=True)
class FontSpec:
    size_px: float
    bold: bool = False
    italic: bool = False


class TextMetrics:
    """Advance-width table. All widths are fractions of the em square."""

    WIDE_PUNCT = set("，。！？；：、（）【】《》「」『』…—·￥")

    def __init__(self, base_size_px: float) -> None:
        self.base_size_px = base_size_px

    def is_wide(self, char: str) -> bool:
        if char in self.WIDE_PUNCT:
            return True
        east_asian = unicodedata.east_asian_width(char)
        return east_asian in ("W", "F")

    def char_advance_em(self, char: str, bold: bool) -> float:
        if char == " ":
            return 0.28
        if char == "\t":
            return 0.56
        if self.is_wide(char):
            return 1.0
        if char.isdigit():
            return 0.58
        if char.isupper():
            return 0.72 + (0.04 if bold else 0.0)
        if char in "iljt.,!:'|":
            return 0.30
        if char in "mwMW":
            return 0.92
        return 0.55 + (0.03 if bold else 0.0)

    def text_width_px(self, text: str, spec: FontSpec) -> float:
        total = sum(self.char_advance_em(char, spec.bold) for char in text)
        return total * spec.size_px

    def cue_line_width_px(self, cue_line: CueLine, size_px: float) -> float:
        return sum(
            self.text_width_px(
                span.text, FontSpec(size_px, span.bold, span.italic))
            for span in cue_line.spans
        )


@dataclass
class Break:
    """One rendered line after wrapping."""
    text: str
    width_px: float
    break_before_index: int
    hard: bool
    timestamp: float | None = None


def wrap_line(cue_line: CueLine, max_width_px: float,
              metrics: TextMetrics, size_px: float,
              base_offset: int = 0) -> list[Break]:
    """Greedy wrap of one hard cue line; CJK may break between any glyphs."""
    text = cue_line.text
    if not text:
        return [Break("", 0.0, 0, hard=True)]

    stamps = sorted(
        (base_offset + offset, stamp) for offset, stamp in cue_line.timestamps
    )
    breaks: list[Break] = []
    start = 0
    current_width = 0.0
    last_break_candidate: int | None = None
    candidate_width = 0.0
    index = 0

    def emit(end: int, width: float, hard: bool) -> None:
        absolute_start = base_offset + start
        stamp = None
        for offset, ts in stamps:
            if offset <= absolute_start:
                stamp = ts
            else:
                break
        breaks.append(Break(text[start:end], width, base_offset + start,
                            hard, stamp))

    while index < len(text):
        char = text[index]
        char_width = metrics.char_advance_em(char, False) * size_px
        at_space = char == " "
        cjk_break = index > start and metrics.is_wide(char)
        if current_width + char_width > max_width_px and index > start:
            if last_break_candidate is not None:
                emit(last_break_candidate, candidate_width, hard=False)
                start = last_break_candidate
                while start < len(text) and text[start] == " ":
                    start += 1
                index = start
            else:
                emit(index, current_width, hard=False)
                start = index
            current_width = 0.0
            last_break_candidate = None
            continue
        current_width += char_width
        if at_space:
            last_break_candidate = index
            candidate_width = current_width
        elif cjk_break and unicodedata.east_asian_width(char) in ("W", "F"):
            last_break_candidate = index
            candidate_width = current_width
        index += 1
    emit(len(text), current_width, hard=False)
    breaks[0].hard = True
    return breaks


def wrap_cue(cue_lines: list[CueLine], max_width_px: float,
             metrics: TextMetrics, size_px: float) -> list[Break]:
    rendered: list[Break] = []
    base_offset = 0
    for cue_line in cue_lines:
        line_breaks = wrap_line(cue_line, max_width_px, metrics, size_px,
                                base_offset)
        if rendered and line_breaks:
            line_breaks[0].hard = True
        rendered.extend(line_breaks)
        base_offset += len(cue_line.text)
    return rendered
