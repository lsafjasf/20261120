from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Callable, Mapping
from urllib.request import Request, urlopen

from .mpd import parse_utc_timestamp
from .model import Presentation, ScheduledEvent, UtcTiming


class UtcTimingError(RuntimeError):
    pass


@dataclass(frozen=True)
class HttpTimeResponse:
    body: str
    headers: Mapping[str, str]


@dataclass(frozen=True)
class ClockResolution:
    scheme_id_uri: str
    value: str
    local_utc: datetime
    server_utc: datetime
    offset_seconds: float


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("clock values must be timezone-aware")
    return value.astimezone(timezone.utc)


def _scheme_kind(scheme: str) -> str:
    for kind in ("direct", "http-head", "http-iso", "http-xsdate"):
        if scheme.endswith(f":{kind}:2014") or scheme.endswith(f":{kind}:2012"):
            return kind
    if scheme.startswith("urn:mpeg:dash:utc:"):
        return scheme.rsplit(":", 2)[-2]
    return scheme


def _default_fetch(url: str, head_only: bool) -> HttpTimeResponse:
    request = Request(url, method="HEAD" if head_only else "GET")
    with urlopen(request, timeout=10) as response:
        body = "" if head_only else response.read().decode("utf-8", "replace")
        headers = {key.lower(): value for key, value in response.headers.items()}
        return HttpTimeResponse(body=body, headers=headers)


class UtcTimingResolver:
    """Resolve wall clock and map it onto the MPD media timeline."""

    def __init__(
        self,
        presentation: Presentation,
        fetch: Callable[[str, bool], HttpTimeResponse] | None = None,
    ) -> None:
        self.presentation = presentation
        self.fetch = fetch or _default_fetch

    def _read(self, timing: UtcTiming) -> datetime:
        kind = _scheme_kind(timing.scheme_id_uri)
        if kind == "direct":
            return parse_utc_timestamp(timing.value)
        if kind in {"http-iso", "http-xsdate"}:
            if not timing.value:
                raise ValueError("HTTP UTCTiming has no URL")
            return parse_utc_timestamp(self.fetch(timing.value, False).body)
        if kind == "http-head":
            if not timing.value:
                raise ValueError("HTTP UTCTiming has no URL")
            headers = self.fetch(timing.value, True).headers
            date_value = next(
                (value for key, value in headers.items() if key.lower() == "date"),
                None,
            )
            if date_value is None:
                raise ValueError("HTTP Date header is missing")
            return parsedate_to_datetime(date_value).astimezone(timezone.utc)
        raise ValueError(f"unsupported UTCTiming scheme: {timing.scheme_id_uri}")

    def resolve(self, local_utc: datetime) -> ClockResolution:
        local = _utc(local_utc)
        errors: list[str] = []
        for timing in self.presentation.utc_timings:
            try:
                server = self._read(timing)
            except Exception as exc:
                errors.append(f"{timing.scheme_id_uri}: {exc}")
                continue
            offset = (server - local).total_seconds()
            return ClockResolution(
                scheme_id_uri=timing.scheme_id_uri,
                value=timing.value,
                local_utc=local,
                server_utc=server,
                offset_seconds=offset,
            )
        detail = "; ".join(errors) if errors else "no UTCTiming elements"
        raise UtcTimingError(f"all UTCTiming sources failed: {detail}")

    def media_time(
        self, local_utc: datetime, resolution: ClockResolution | None = None
    ) -> float:
        clock = resolution or self.resolve(local_utc)
        corrected = _utc(local_utc) + timedelta(seconds=clock.offset_seconds)
        return (
            corrected - self.presentation.availability_start_time
        ).total_seconds()

    def event_utc(self, event: ScheduledEvent) -> datetime:
        return self.presentation.availability_start_time + timedelta(
            seconds=event.start
        )
