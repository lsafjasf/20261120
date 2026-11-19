import math
import unittest

from src.spatial_audio.reverb import Reverb
from src.spatial_audio.wavio import sine_sweep


class ReverbTailTests(unittest.TestCase):
    def _endpoint_for_rt60(self, rt60):
        sr = 16000
        rev = Reverb(sr, rt60_s=rt60, wet=0.5)
        dry = sine_sweep(0.3, sr)
        out = rev.process(dry)
        return rev, out

    def test_low_rt60_tail_is_silent_and_smooth(self):
        rev, out = self._endpoint_for_rt60(0.08)
        self.assertLess(rev.tail_endpoint_db(out), -70.0)
        # no discontinuity at the input/tail boundary
        boundary = int(0.3 * 16000)
        jump = abs(out[boundary + 1] - out[boundary - 1])
        self.assertLess(jump, 0.05)

    def test_tail_release_is_smooth(self):
        rev, out = self._endpoint_for_rt60(0.25)
        # The raised-cosine release covers the final third of the tail.
        # The ring signal keeps oscillating, so assert bounded per-sample
        # motion (no click) and an exact, silent endpoint instead.
        tail_len = len(out) - int(0.3 * 16000)
        release = out[len(out) - tail_len // 3:]
        peak = max(abs(v) for v in out)
        max_jump = max(abs(b - a) for a, b in zip(release, release[1:]))
        self.assertLess(max_jump, 0.02 * peak)
        self.assertEqual(out[-1], 0.0)

    def test_feedback_matches_rt60(self):
        rev = Reverb(16000, rt60_s=0.3)
        g = rev.combs[0].g
        delay = len(rev.combs[0].buf) / 16000
        measured = -3.0 * delay / math.log10(g)
        self.assertAlmostEqual(measured, 0.3, places=2)

    def test_impulse_decay_matches_rt60(self):
        sr = 16000
        rev = Reverb(sr, rt60_s=0.4, wet=1.0)
        out = rev.process([1.0] + [0.0] * int(0.05 * sr))
        # Schroeder backward-integrated energy (robust against comb beating)
        start = int(0.05 * sr)
        squared = [v * v for v in out[start:]]
        integrated = []
        acc = 0.0
        for e in reversed(squared):
            acc += e
            integrated.append(acc)
        integrated.reverse()
        peak = integrated[0]
        pts = [
            (i / sr, 10 * math.log10(max(v / peak, 1e-12)))
            for i, v in enumerate(integrated)
            if -35 <= 10 * math.log10(max(v / peak, 1e-12)) <= -5
        ]
        n = len(pts)
        sx = sum(t for t, _ in pts)
        sy = sum(y for _, y in pts)
        sxx = sum(t * t for t, _ in pts)
        sxy = sum(t * y for t, y in pts)
        slope = (n * sxy - sx * sy) / (n * sxx - sx * sx)
        measured = -60.0 / slope
        self.assertAlmostEqual(measured, 0.4, delta=0.08)


if __name__ == "__main__":
    unittest.main()
