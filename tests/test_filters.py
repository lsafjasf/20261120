import math
import unittest

from src.spatial_audio.filters import (
    DirectFilter,
    OneEuroFilter,
    angle_diff,
    wrap_angle,
)


class AngleTests(unittest.TestCase):
    def test_wrap(self):
        self.assertAlmostEqual(wrap_angle(3 * math.pi), -math.pi)
        self.assertAlmostEqual(wrap_angle(-3 * math.pi), -math.pi)
        self.assertAlmostEqual(angle_diff(math.pi - 0.01, -math.pi + 0.01),
                               -0.02, places=4)


class FilterTests(unittest.TestCase):
    def test_direct_passthrough(self):
        f = DirectFilter()
        for x in (0.1, -0.4, 1.2):
            self.assertEqual(f(0.01, x), wrap_angle(x))

    def test_one_euro_rejects_static_jitter(self):
        f = OneEuroFilter(min_cutoff=0.8, beta=0.05)
        rng = __import__("random").Random(7)
        outs = [f(i / 60.0, rng.gauss(0.0, math.radians(2.0)))
                for i in range(300)]
        std = math.sqrt(sum(o * o for o in outs) / len(outs))
        self.assertLess(math.degrees(std), 0.6)

    def test_one_euro_tracks_fast_ramp_with_small_lag(self):
        f = OneEuroFilter(min_cutoff=1.0, beta=0.35)
        dt = 1 / 60.0
        lag_deg = []
        for i in range(300):
            t = i * dt
            true = 3.0 * t
            out = f(t, true)
            lag_deg.append(math.degrees(angle_diff(true, out)))
        # During the constant-velocity portion lag must stay small.
        steady = lag_deg[100:250]
        self.assertLess(max(steady), 7.0)

    def test_cutoff_adapts_to_speed(self):
        f = OneEuroFilter()
        f(0.0, 0.0)
        for i in range(1, 10):
            f(i / 60, 0.001)
        slow_cutoff = f.last_cutoff
        for i in range(10, 40):
            f(i / 60, 3.0 * i / 60)
        fast_cutoff = f.last_cutoff
        self.assertGreater(fast_cutoff, slow_cutoff * 2)


if __name__ == "__main__":
    unittest.main()
