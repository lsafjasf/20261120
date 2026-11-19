"""Regression tests for the caption pipeline and RTSP recovery."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from samples.make_sample import build_stream
from cc_pipeline.errors import ErrorLog
from cc_pipeline.mpeg2 import extract_cc_pairs
from cc_pipeline.cea608 import decode_608
from cc_pipeline.cea708 import decode_708
from cc_pipeline.overlay import compose
from cc_pipeline.rtsp_sim import simulate_outage


class PipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data, cls.expected_errors = build_stream()
        cls.log = ErrorLog()
        pairs = extract_cc_pairs(cls.data, cls.log)
        cls.ev608, cls.st608 = decode_608(pairs, cls.log)
        cls.ev708, cls.st708 = decode_708(pairs, cls.log)
        cls.events = cls.ev608 + cls.ev708

    def test_608_popon_event(self):
        ev = next(e for e in self.ev608 if e.source == "608-CC1")
        self.assertEqual((ev.start_frame, ev.end_frame), (14, 60))
        self.assertEqual(ev.rows, (15,))
        self.assertIn("MAIN", ev.text)

    def test_608_rollup_dual_channel(self):
        subs = [e for e in self.ev608 if e.source == "608-CC3"]
        self.assertEqual(len(subs), 2)
        self.assertEqual(subs[0].rows, (1,))
        self.assertEqual(subs[1].rows, (2,))
        self.assertEqual(subs[0].text, "sub one")
        self.assertEqual(subs[1].text, "sub two")

    def test_708_event_and_region(self):
        ev = self.ev708[0]
        self.assertEqual((ev.start_frame, ev.end_frame), (31, 56))
        self.assertEqual(ev.text, "708 PRIMARY MORE")
        self.assertGreater(ev.region[0], 50)     # bottom-anchored window

    def test_style_switches(self):
        descs = [d for _, _, d in self.st608 + self.st708]
        self.assertIn("white+italics", descs)
        self.assertIn("white+italics+underline", descs)

    def test_error_offsets_match_injected(self):
        detected = [e.offset for e in self.log.errors]
        expected = [off for off, _ in self.expected_errors]
        self.assertEqual(sorted(detected), sorted(expected))

    def test_overlay_no_main_sub_conflict(self):
        _, conflicts, _, suppressed = compose(self.events)
        self.assertEqual(conflicts, [])
        self.assertTrue(any("608-CC1" in s for s in suppressed))

    def test_rtsp_recovery(self):
        checks, _ = simulate_outage()
        self.assertTrue(all(ok for _, ok in checks))


if __name__ == "__main__":
    unittest.main()
