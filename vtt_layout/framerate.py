"""Compare declared script-tag frame rate with timestamp-derived rate."""

from __future__ import annotations

import html.parser
import re
from dataclasses import dataclass
from fractions import Fraction

from .parser import parse_timestamp


class _ScriptExtractor(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.declared_fps: float | None = None
        self.vtt_payload: str | None = None
        self._capture = False
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "script":
            return
        attr = dict(attrs)
        if attr.get("data-fps"):
            self.declared_fps = float(attr["data-fps"])
        if attr.get("type") == "text/vtt":
            self._capture = True
            self._buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._capture:
            self.vtt_payload = "".join(self._buf)
            self._capture = False

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._buf.append(data)


def extract_script_tag(html_text: str) -> tuple[float | None, str | None]:
    parser = _ScriptExtractor()
    parser.feed(html_text)
    return parser.declared_fps, parser.vtt_payload


TS_TOKEN_RE = re.compile(r"(?<!\d)(\d{1,2}:)?\d{1,2}:\d{2}\.\d{1,3}")


def collect_timestamp_seconds(vtt_text: str) -> list[float]:
    seconds = [parse_timestamp(match.group(0))
               for match in TS_TOKEN_RE.finditer(vtt_text)]
    return sorted(set(round(value, 3) for value in seconds))


def derive_frame_rate(seconds: list[float],
                      candidates: tuple[float, ...] = (
                          23.976, 24.0, 25.0, 29.97, 30.0, 50.0, 59.94, 60.0)) -> float:
    """Pick the standard fps whose integer frame grid fits every timestamp."""
    best_rate = 0.0
    best_error = float("inf")
    for rate in candidates:
        max_error = 0.0
        for value in seconds:
            frame_index = round(value * rate)
            max_error = max(max_error, abs(frame_index / rate - value))
        if max_error < best_error:
            best_error = max_error
            best_rate = rate
    return best_rate if best_error <= 1.0 / (best_rate * 4.0) else float("nan")


@dataclass
class FrameRateVerdict:
    declared_fps: float | None
    derived_fps: float
    tolerance: float
    consistent: bool
    max_grid_error_s: float
    timestamp_count: int


def check_frame_rate(html_text: str, tolerance: float = 0.05) -> FrameRateVerdict:
    declared, payload = extract_script_tag(html_text)
    if payload is None:
        raise ValueError("no <script type=\"text/vtt\"> block found")
    seconds = collect_timestamp_seconds(payload)
    derived = derive_frame_rate(seconds)
    max_error = max(
        abs(round(value * derived) / derived - value) for value in seconds
    )
    consistent = (
        declared is not None
        and derived == derived  # NaN guard
        and abs(declared - derived) <= tolerance
    )
    return FrameRateVerdict(declared, derived, tolerance, consistent,
                            max_error, len(seconds))


def fractional_gcd(values: list[Fraction]) -> Fraction:
    result = values[0]
    for value in values[1:]:
        while value:
            result, value = value, result % value
    return result
