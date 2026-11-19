"""Self-checks: scan bands detected, static/moving controls not flagged."""

import unittest

from ledwall_precheck.analyze import detect_bands, scan_rate_candidates
from ledwall_precheck.synth import (render_gradient, render_moving_object,
                                    render_scan_clip, render_static_bands)

FPS = 48.0
N = 32


class ScanBandDetection(unittest.TestCase):
    def _scan(self, scan_hz, axis):
        frames = render_scan_clip(N, FPS, 1 / 9600, 0.0, 1.0 / scan_hz, 0.8,
                                  scan_axis=axis)
        return detect_bands(frames, axis)

    def test_vertical_down(self):
        res = self._scan(48 * (20 + 6 / 256), "row")
        self.assertEqual(res["status"], "scan_band")
        self.assertEqual(res["direction"], "down")
        self.assertAlmostEqual(res["velocity_lines_per_frame"], 6.0, delta=1.0)
        self.assertAlmostEqual(res["period_frames"], 256 / 6, delta=6.0)

    def test_vertical_up(self):
        res = self._scan(48 * (20 - 6 / 256), "row")
        self.assertEqual(res["status"], "scan_band")
        self.assertEqual(res["direction"], "up")

    def test_horizontal_right(self):
        res = self._scan(48 * (20 + 6 / 384), "col")
        self.assertEqual(res["status"], "scan_band")
        self.assertEqual(res["direction"], "right")

    def test_horizontal_left(self):
        res = self._scan(48 * (20 - 6 / 384), "col")
        self.assertEqual(res["status"], "scan_band")
        self.assertEqual(res["direction"], "left")


class ControlsNotFlagged(unittest.TestCase):
    def test_gradient(self):
        res = detect_bands(render_gradient(N), "row")
        self.assertNotEqual(res["status"], "scan_band")

    def test_static_bands(self):
        res = detect_bands(render_static_bands(N), "row")
        self.assertNotEqual(res["status"], "scan_band")

    def test_moving_object(self):
        res = detect_bands(render_moving_object(N), "row")
        self.assertEqual(res["status"], "localized_object")


class Ambiguity(unittest.TestCase):
    def test_aliases_include_truth(self):
        cands = scan_rate_candidates(6.0, 256, FPS)
        self.assertGreater(cands["count"], 1)
        truth = 48 * (20 + 6 / 256)
        self.assertTrue(any(abs(f - truth) < 0.5
                            for f in cands["frequencies_hz"]))


if __name__ == "__main__":
    unittest.main()
