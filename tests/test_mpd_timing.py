from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

from dash_events.mpd import parse_iso8601_duration, parse_mpd
from dash_events.timeline import align_events
from dash_events.timing import HttpTimeResponse, UtcTimingResolver

ROOT = Path(__file__).resolve().parents[1]


class MpdTimingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.presentation = parse_mpd(ROOT / "fixtures/two_period.mpd")
        self.events = {event.key.event_id: event for event in align_events(self.presentation)}

    def test_periods_are_contiguous_and_half_open(self) -> None:
        p0, p1 = self.presentation.periods
        self.assertEqual((p0.start, p0.end), (0.0, 10.0))
        self.assertEqual((p1.start, p1.end), (10.0, 20.0))
        self.assertEqual(self.presentation.period_at(9.999).id, "p0")
        self.assertEqual(self.presentation.period_at(10.0).id, "p1")

    def test_presentation_time_offset_aligns_event_to_media_time(self) -> None:
        event = self.events[1002]
        self.assertAlmostEqual(event.start, 9.0)
        self.assertAlmostEqual(event.end, 12.0)
        self.assertEqual(event.owner_period_id, "p0")
        self.assertEqual(event.declared_period_ids, ("p0", "p1"))

    def test_boundary_event_belongs_to_p1(self) -> None:
        event = self.events[1003]
        self.assertAlmostEqual(event.start, 10.0)
        self.assertEqual(event.owner_period_id, "p1")

    def test_utc_timing_maps_clock_to_media_time(self) -> None:
        resolver = UtcTimingResolver(self.presentation)
        local = datetime(2026, 1, 1, 0, 0, 5, tzinfo=timezone.utc)
        clock = resolver.resolve(local)
        self.assertEqual(clock.scheme_id_uri, "urn:mpeg:dash:utc:direct:2014")
        self.assertAlmostEqual(clock.offset_seconds, 2.0)
        self.assertAlmostEqual(resolver.media_time(local, clock), 7.0)
        event_utc = resolver.event_utc(self.events[1002])
        self.assertEqual(
            event_utc,
            datetime(2026, 1, 1, 0, 0, 9, tzinfo=timezone.utc),
        )

    def test_utc_timing_falls_back_to_next_source(self) -> None:
        presentation = parse_mpd(ROOT / "fixtures/utc_timing_fallback.mpd")
        calls: list[tuple[str, bool]] = []

        def fetch(url: str, head_only: bool) -> HttpTimeResponse:
            calls.append((url, head_only))
            return HttpTimeResponse(body="", headers={})

        resolver = UtcTimingResolver(presentation, fetch=fetch)
        clock = resolver.resolve(datetime(2026, 1, 1, 0, 0, 5, tzinfo=timezone.utc))
        self.assertEqual(clock.scheme_id_uri, "urn:mpeg:dash:utc:direct:2014")
        self.assertEqual(calls, [("https://clock.invalid/time", True)])

    def test_iso8601_duration_parser(self) -> None:
        self.assertAlmostEqual(parse_iso8601_duration("PT1H2M3.5S"), 3723.5)
        self.assertAlmostEqual(parse_iso8601_duration("P1DT0.5S"), 86400.5)


if __name__ == "__main__":
    unittest.main()
