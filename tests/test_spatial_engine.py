import math
import unittest

from src.spatial_audio.engine import OrientationEvent, SpatialEngine
from src.spatial_audio.filters import DirectFilter, OneEuroFilter
from src.spatial_audio.spatial import SlewLimiter, equal_power_gains


class SpatialTests(unittest.TestCase):
    def test_equal_power_constant_loudness(self):
        for az in [x * 0.1 for x in range(-15, 16)]:
            gl, gr = equal_power_gains(az)
            self.assertAlmostEqual(gl * gl + gr * gr, 1.0, places=9)

    def test_slew_limiter_bounds_rate(self):
        lim = SlewLimiter(max_rate_per_s=0.5, initial=1.0)
        out = [lim.update(i * 0.1, 2.0) for i in range(20)]
        for a, b in zip(out, out[1:]):
            self.assertLessEqual(abs(b - a), 0.5 * 0.1 + 1e-12)
        # first tick uses the nominal render period (1/120 s), then 19
        # regular 0.1 s steps, each capped at rate * dt.
        self.assertAlmostEqual(out[-1], 1.0 + 0.5 * (1.0 / 120.0 + 19 * 0.1),
                               places=4)

    def test_engine_latency_is_bounded_by_render_period(self):
        engine = SpatialEngine(DirectFilter(), render_hz=120.0)
        for i in range(120):
            engine.push_event(OrientationEvent(i / 60.0, 0.01 * (i % 3), i))
        engine.drain(2.0)
        stats = engine.latency_statistics()
        self.assertEqual(stats["n"], 120.0)
        self.assertLessEqual(stats["max_ms"], 1000.0 / 60.0 + 1000.0 / 120.0 + 1e-6)
        self.assertGreaterEqual(stats["min_ms"], 0.0)

    def test_doppler_slew_removes_jumps(self):
        def yaw_at(t):
            # abrupt direction reversal -> raw doppler would jump
            return 2.0 * t if t < 0.5 else 2.0 - 2.0 * (t - 0.5)

        slew_engine = SpatialEngine(
            DirectFilter(), render_hz=120.0, doppler_slew_rate=1.0
        )
        raw_engine = SpatialEngine(
            DirectFilter(), render_hz=120.0, doppler_slew_enabled=False
        )
        for i in range(120):
            t = i / 60.0
            ev = OrientationEvent(t, yaw_at(t), i)
            slew_engine.push_event(ev)
            raw_engine.push_event(OrientationEvent(t, yaw_at(t), i))
        slew_states = slew_engine.drain(1.0)
        raw_states = raw_engine.drain(1.0)

        def max_jump(states, key):
            cents = [1200 * math.log2(getattr(s, key)) for s in states]
            return max(abs(b - a) for a, b in zip(cents, cents[1:]))

        self.assertLess(max_jump(slew_states, "doppler_applied"),
                        max_jump(raw_states, "doppler_target") * 0.6)

    def test_smoothing_does_not_add_pipeline_latency(self):
        direct = SpatialEngine(DirectFilter(), render_hz=120.0)
        smooth = SpatialEngine(OneEuroFilter(), render_hz=120.0)
        for i in range(240):
            ev = OrientationEvent(i / 60.0, 0.001 * i, i)
            direct.push_event(ev)
            smooth.push_event(OrientationEvent(i / 60.0, 0.001 * i, i))
        direct.drain(2.0)
        smooth.drain(2.0)
        d, s = direct.latency_statistics(), smooth.latency_statistics()
        # Pipeline (capture->apply) latency is identical: filter runs inside
        # the same render tick, adding zero queued delay.
        self.assertAlmostEqual(d["p95_ms"], s["p95_ms"], places=6)
        self.assertAlmostEqual(d["max_ms"], s["max_ms"], places=6)


if __name__ == "__main__":
    unittest.main()
