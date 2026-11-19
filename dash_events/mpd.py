from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from .model import (
    EventKey,
    EventSignal,
    EventStream,
    Period,
    Presentation,
    UtcTiming,
)

_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+(?:\.\d+)?)D)?"
    r"(?:T(?:(?P<hours>\d+(?:\.\d+)?)H)?"
    r"(?:(?P<minutes>\d+(?:\.\d+)?)M)?"
    r"(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$"
)


def parse_iso8601_duration(value: str) -> float:
    match = _DURATION_RE.fullmatch(value.strip())
    if not match or value.strip() in {"P", "PT"}:
        raise ValueError(f"unsupported ISO 8601 duration: {value!r}")
    parts = {name: float(number or 0) for name, number in match.groupdict().items()}
    return (
        parts["days"] * 86400
        + parts["hours"] * 3600
        + parts["minutes"] * 60
        + parts["seconds"]
    )


def parse_utc_timestamp(value: str) -> datetime:
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError(f"UTC timestamp has no timezone: {value!r}")
    return parsed.astimezone(timezone.utc)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in element if _local_name(child.tag) == name]


def _required(element: ET.Element, name: str) -> str:
    value = element.get(name)
    if value is None:
        raise ValueError(f"<{_local_name(element.tag)}> is missing @{name}")
    return value


def _parse_event_stream(
    element: ET.Element,
    period_id: str,
    ordinal_start: int,
) -> tuple[EventStream, int]:
    scheme = _required(element, "schemeIdUri")
    value = element.get("value", "")
    timescale = int(element.get("timescale", "1"))
    pto = int(element.get("presentationTimeOffset", "0"))
    if timescale <= 0:
        raise ValueError("EventStream@timescale must be positive")

    events: list[EventSignal] = []
    ordinal = ordinal_start
    for event in _children(element, "Event"):
        event_id = int(event.get("id", "0"))
        events.append(
            EventSignal(
                key=EventKey(scheme, value, event_id),
                period_id=period_id,
                presentation_time=int(event.get("presentationTime", "0")),
                duration=int(event.get("duration", "0")),
                timescale=timescale,
                presentation_time_offset=pto,
                message_data=event.text or "",
                ordinal=ordinal,
            )
        )
        ordinal += 1
    return (
        EventStream(scheme, value, timescale, pto, tuple(events)),
        ordinal,
    )


def _parse_period(element: ET.Element, ordinal: int) -> tuple[Period, int, float | None]:
    period_id = _required(element, "id")
    explicit_start = element.get("start")
    explicit_duration = element.get("duration")
    streams: list[EventStream] = []
    for stream_element in _children(element, "EventStream"):
        stream, ordinal = _parse_event_stream(stream_element, period_id, ordinal)
        streams.append(stream)
    duration = (
        parse_iso8601_duration(explicit_duration)
        if explicit_duration is not None
        else None
    )
    period = Period(
        id=period_id,
        start=parse_iso8601_duration(explicit_start) if explicit_start else -1.0,
        duration=duration or 0.0,
        event_streams=tuple(streams),
    )
    return period, ordinal, duration


def parse_mpd(path: str | Path) -> Presentation:
    root = ET.parse(path).getroot()
    if _local_name(root.tag) != "MPD":
        raise ValueError("root element is not MPD")
    if _children(root, "EventStream"):
        raise ValueError(
            "MPD-level EventStream is not supported: MPD events must be "
            "carried by Period-level EventStream elements"
        )

    availability = parse_utc_timestamp(_required(root, "availabilityStartTime"))
    mpd_duration = parse_iso8601_duration(_required(root, "mediaPresentationDuration"))
    timings = tuple(
        UtcTiming(
            scheme_id_uri=_required(element, "schemeIdUri"),
            value=element.get("value", ""),
        )
        for element in _children(root, "UTCTiming")
    )

    raw_periods: list[Period] = []
    explicit_durations: list[float | None] = []
    ordinal = 0
    for element in _children(root, "Period"):
        period, ordinal, duration = _parse_period(element, ordinal)
        raw_periods.append(period)
        explicit_durations.append(duration)

    periods: list[Period] = []
    for index, raw in enumerate(raw_periods):
        if raw.start < 0:
            start = periods[-1].end if periods else 0.0
        else:
            start = raw.start
        if explicit_durations[index] is not None:
            duration = explicit_durations[index]
        elif index + 1 < len(raw_periods):
            next_start = raw_periods[index + 1].start
            if next_start < 0:
                raise ValueError("cannot infer Period duration without next start")
            duration = next_start - start
        else:
            duration = mpd_duration - start
        if duration is None or duration <= 0:
            raise ValueError(f"Period {raw.id!r} has no positive duration")
        periods.append(Period(raw.id, start, duration, raw.event_streams))

    for previous, current in zip(periods, periods[1:]):
        if abs(previous.end - current.start) > 1e-9:
            raise ValueError(
                f"Periods are not contiguous: {previous.id!r} ends at "
                f"{previous.end:g}s but {current.id!r} starts at "
                f"{current.start:g}s"
            )

    return Presentation(
        id=root.get("id", ""),
        availability_start_time=availability,
        duration=mpd_duration,
        periods=tuple(periods),
        utc_timings=timings,
    )
