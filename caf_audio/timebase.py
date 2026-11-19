"""SMPTE timecode conversion for the four requested time bases."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import math

from .errors import TimecodeError


@dataclass(frozen=True)
class TimeBase:
    name: str
    nominal_fps: int
    rate: Fraction
    drop_frame: bool = False

    @property
    def fps(self) -> float:
        return float(self.rate)

    @property
    def separator(self) -> str:
        return ";" if self.drop_frame else ":"

    @property
    def frames_per_day(self) -> int:
        if self.drop_frame:
            return 24 * 6 * 17982
        return self.nominal_fps * 60 * 60 * 24


FILM_24 = TimeBase("24", 24, Fraction(24))
PAL_25 = TimeBase("25", 25, Fraction(25))
NTSC_30 = TimeBase("30", 30, Fraction(30))
NTSC_30_DROP = TimeBase(
    "30-drop", 30, Fraction(30000, 1001), drop_frame=True
)
FOUR_TIMEBASES = (FILM_24, PAL_25, NTSC_30, NTSC_30_DROP)


@dataclass(frozen=True, order=True)
class Timecode:
    hours: int
    minutes: int
    seconds: int
    frames: int

    def __post_init__(self) -> None:
        if not 0 <= self.hours <= 24:
            raise TimecodeError(f"hours out of range: {self.hours}")
        if not 0 <= self.minutes < 60:
            raise TimecodeError(f"minutes out of range: {self.minutes}")
        if not 0 <= self.seconds < 60:
            raise TimecodeError(f"seconds out of range: {self.seconds}")
        if not 0 <= self.frames < 60:
            raise TimecodeError(f"frames out of range: {self.frames}")

    def format(self, separator: str = ":") -> str:
        return (
            f"{self.hours:02d}:{self.minutes:02d}:{self.seconds:02d}"
            f"{separator}{self.frames:02d}"
        )

    def __str__(self) -> str:
        return self.format()


def parse_timecode(text: str, timebase: TimeBase) -> Timecode:
    parts = text.replace(";", ":").split(":")
    if len(parts) != 4 or any(not part.isdigit() for part in parts):
        raise TimecodeError(
            f"timecode must be HH:MM:SS:FF, got {text!r}"
        )
    hours, minutes, seconds, frames = map(int, parts)
    if timebase.drop_frame and text[-3] != ";":
        raise TimecodeError(
            f"drop-frame timecode must use ';' before frames: {text!r}"
        )
    if frames >= timebase.nominal_fps:
        raise TimecodeError(
            f"frame number {frames} is outside 0..{timebase.nominal_fps - 1}"
        )
    timecode = Timecode(hours, minutes, seconds, frames)
    timecode_to_frames(timecode, timebase)
    return timecode


def frames_to_timecode(frame_number: int, timebase: TimeBase) -> Timecode:
    if not isinstance(frame_number, int):
        raise TimecodeError("frame number must be an integer")
    if frame_number < 0:
        raise TimecodeError("frame number must be non-negative")
    if frame_number >= timebase.frames_per_day:
        raise TimecodeError(
            f"frame {frame_number} exceeds the {timebase.name} day boundary"
        )

    nominal = frame_number
    if timebase.drop_frame:
        ten_minute_blocks, remainder = divmod(nominal, 17982)
        nominal += 18 * ten_minute_blocks
        if remainder >= 2:
            nominal += 2 * ((remainder - 2) // 1798)

    frames = nominal % timebase.nominal_fps
    total_seconds = nominal // timebase.nominal_fps
    seconds = total_seconds % 60
    total_minutes = total_seconds // 60
    minutes = total_minutes % 60
    hours = (total_minutes // 60) % 24
    return Timecode(hours, minutes, seconds, frames)


def timecode_to_frames(timecode: Timecode, timebase: TimeBase) -> int:
    if timecode.hours == 24:
        if any((timecode.minutes, timecode.seconds, timecode.frames)):
            raise TimecodeError("24:00:00:00 is the only valid 24-hour label")
        return timebase.frames_per_day
    if timecode.frames >= timebase.nominal_fps:
        raise TimecodeError(
            f"frame number {timecode.frames} is outside "
            f"0..{timebase.nominal_fps - 1}"
        )
    if (
        timebase.drop_frame
        and timecode.seconds == 0
        and timecode.minutes % 10
        and timecode.frames < 2
    ):
        raise TimecodeError(
            "drop-frame labels :00 and :01 do not exist except at "
            "10-minute boundaries"
        )

    nominal = (
        ((timecode.hours * 60 + timecode.minutes) * 60 + timecode.seconds)
        * timebase.nominal_fps
        + timecode.frames
    )
    if timebase.drop_frame:
        total_minutes = timecode.hours * 60 + timecode.minutes
        nominal -= 2 * (total_minutes - total_minutes // 10)
    return nominal


def format_frame(frame_number: int, timebase: TimeBase) -> str:
    return frames_to_timecode(frame_number, timebase).format(timebase.separator)


def seconds_to_frames(
    seconds: float | int | Fraction,
    timebase: TimeBase,
    rounding: str = "nearest",
) -> int:
    exact = seconds if isinstance(seconds, Fraction) else Fraction(seconds)
    if exact < 0:
        raise TimecodeError("seconds must be non-negative")
    scaled = exact * timebase.rate
    if rounding == "floor":
        return math.floor(scaled)
    if rounding == "ceil":
        return math.ceil(scaled)
    if rounding == "nearest":
        return round(scaled)
    raise TimecodeError(f"unknown rounding mode: {rounding}")


def frames_to_seconds(
    frame_number: int, timebase: TimeBase
) -> Fraction:
    if frame_number < 0:
        raise TimecodeError("frame number must be non-negative")
    return Fraction(frame_number) / timebase.rate


def convert_timecode(
    timecode: Timecode,
    source: TimeBase,
    target: TimeBase,
    rounding: str = "nearest",
) -> Timecode:
    source_frames = timecode_to_frames(timecode, source)
    if source_frames == source.frames_per_day:
        return Timecode(24, 0, 0, 0)
    seconds = frames_to_seconds(source_frames, source)
    target_frames = seconds_to_frames(seconds, target, rounding)
    if target_frames >= target.frames_per_day:
        raise TimecodeError(
            f"converted frame {target_frames} exceeds the "
            f"{target.name} day boundary"
        )
    return frames_to_timecode(target_frames, target)


def _entry(name: str, frame: int, timebase: TimeBase) -> dict:
    if frame == timebase.frames_per_day:
        timecode = "24:00:00:00"
    else:
        tc = frames_to_timecode(frame, timebase)
        timecode = tc.format(timebase.separator)
    return {
        "name": name,
        "frame": frame,
        "timecode": timecode,
        "seconds": float(frames_to_seconds(frame, timebase)),
    }


def boundary_values() -> list[dict]:
    rows: list[dict] = []
    for base in FOUR_TIMEBASES:
        boundary_frames = {
            "origin": 0,
            "one_second": base.nominal_fps,
            "one_minute": base.nominal_fps * 60,
            "ten_minutes": base.nominal_fps * 600,
            "one_hour": base.nominal_fps * 3600,
            "day_rollover": base.frames_per_day,
        }
        if base.drop_frame:
            boundary_frames.update(
                {
                    "one_minute": 1800,
                    "ten_minutes": 17982,
                    "one_hour": 107892,
                }
            )
        for label, frame in boundary_frames.items():
            rows.append({"timebase": base.name, **_entry(label, frame, base)})
    return rows


def drop_frame_checks() -> dict:
    last = timecode_to_frames(Timecode(0, 0, 59, 29), NTSC_30_DROP)
    first_dropped_minute = timecode_to_frames(
        Timecode(0, 1, 0, 2), NTSC_30_DROP
    )
    tenth_minute = timecode_to_frames(Timecode(0, 10, 0, 0), NTSC_30_DROP)
    hour = timecode_to_frames(Timecode(1, 0, 0, 0), NTSC_30_DROP)
    return {
        "before_first_drop": {
            "frame": last,
            "timecode": format_frame(last, NTSC_30_DROP),
        },
        "after_first_drop": {
            "frame": first_dropped_minute,
            "timecode": format_frame(first_dropped_minute, NTSC_30_DROP),
        },
        "invalid_labels": ["00:01:00:00", "00:01:00:01"],
        "tenth_minute_boundary": {
            "frame": tenth_minute,
            "timecode": format_frame(tenth_minute, NTSC_30_DROP),
        },
        "hour_boundary": {
            "frame": hour,
            "timecode": format_frame(hour, NTSC_30_DROP),
        },
        "dropped_labels_per_hour": 108,
        "frames_per_hour": hour,
        "frames_per_day": NTSC_30_DROP.frames_per_day,
    }


def conversion_boundaries() -> list[dict]:
    rows: list[dict] = []
    for source in FOUR_TIMEBASES:
        for seconds in (0, 1, 60, 600, 3600):
            source_frame = seconds_to_frames(seconds, source)
            source_tc = frames_to_timecode(source_frame, source)
            conversions = {}
            for target in FOUR_TIMEBASES:
                target_tc = convert_timecode(source_tc, source, target)
                conversions[target.name] = {
                    "frame": timecode_to_frames(target_tc, target),
                    "timecode": target_tc.format(target.separator),
                }
            rows.append(
                {
                    "source": source.name,
                    "seconds": seconds,
                    "source_frame": source_frame,
                    "source_timecode": source_tc.format(source.separator),
                    "targets": conversions,
                }
            )
    return rows
