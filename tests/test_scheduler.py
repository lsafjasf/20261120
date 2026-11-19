from __future__ import annotations

import unittest
from pathlib import Path

from dash_events.mpd import parse_mpd
from dash_events.scheduler import EventTimeline

ROOT = Path(__file__).resolve().parents[1]


class SchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.presentation = parse_mpd(ROOT / "fixtures/two_period.mpd")

    def test_continuous_playback_triggers_each_event_once(self) -> None:
        result = EventTimeline(self.presentation).advance_through(20.0, 0.25)
        self.assertEqual(
            [dispatch.key.event_id for dispatch in result.dispatches],
            [1001, 1004, 1002, 1003, 1005],
        )
        self.assertTrue(
            all(state.trigger_count == 1 for state in result.states.values())
        )

    def test_large_time_jump_still_processes_intermediate_events(self) -> None:
        result = EventTimeline(self.presentation).advance_to(20.0)
        self.assertEqual(
            [dispatch.key.event_id for dispatch in result.dispatches],
            [1001, 1004, 1002, 1003, 1005],
        )

    def test_cross_period_event_is_inherited_without_retrigger(self) -> None:
        result = EventTimeline(self.presentation).advance_through(20.0, 0.25)
        state = result.states[next(key for key in result.states if key.event_id == 1002)]
        self.assertEqual(state.trigger_count, 1)
        self.assertAlmostEqual(state.inherited_at or 0.0, 10.0)
        self.assertEqual(state.inherited_from, "p0")
        self.assertEqual(state.post_switch_triggers, 0)
        self.assertAlmostEqual(state.expired_at or 0.0, 12.0)
        transition = result.transitions[0]
        self.assertIn(
            1002, [key.event_id for key in transition.inherited_keys]
        )

    def test_expired_event_is_not_retriggered_from_stale_p1_signal(self) -> None:
        result = EventTimeline(self.presentation).advance_through(20.0, 0.25)
        state = result.states[next(key for key in result.states if key.event_id == 1004)]
        self.assertEqual(state.trigger_count, 1)
        self.assertEqual(state.post_switch_triggers, 0)
        self.assertAlmostEqual(state.expired_at or 0.0, 5.5)

    def test_fresh_p1_join_skips_expired_event_and_dispatches_continuation(self) -> None:
        boundary = self.presentation.periods[1].start
        result = EventTimeline(self.presentation, start=boundary).advance_through(
            20.0, 0.25
        )
        self.assertEqual(
            [dispatch.key.event_id for dispatch in result.dispatches],
            [1002, 1003, 1005],
        )
        self.assertTrue(
            all(dispatch.inherited for dispatch in result.dispatches[:1])
        )
        expired = result.states[
            next(key for key in result.states if key.event_id == 1004)
        ]
        self.assertEqual(expired.trigger_count, 0)

    def test_exact_boundary_event_is_dispatched_in_p1(self) -> None:
        result = EventTimeline(self.presentation).advance_through(14.0, 0.25)
        dispatch = next(
            dispatch for dispatch in result.dispatches if dispatch.key.event_id == 1003
        )
        self.assertEqual(dispatch.dispatch_period_id, "p1")
        self.assertEqual(dispatch.owner_period_id, "p1")
        self.assertFalse(dispatch.inherited)


if __name__ == "__main__":
    unittest.main()
