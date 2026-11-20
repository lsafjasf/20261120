"""Unit tests for the shutter integrator (stdlib unittest)."""

import unittest

import numpy as np

from shutter_integrator import (
    TRIANGLE,
    UNIFORM,
    OutputFrame,
    SubFrame,
    Manifest,
    integrate_frames,
)
from shutter_integrator.scenes import (
    MovingBarScene,
    OcclusionScene,
    StaticScene,
    dense_reference,
)
from shutter_integrator.validate import measure_support_width
from shutter_integrator.weights import integrate_weight, total_weight


class TestWeights(unittest.TestCase):
    def test_uniform_total(self):
        self.assertAlmostEqual(total_weight(UNIFORM, 0.2, 0.7), 0.5)

    def test_triangle_total(self):
        self.assertAlmostEqual(total_weight(TRIANGLE, 0.0, 1.0), 0.5)

    def test_integrate_weight_clips_to_shutter(self):
        w = integrate_weight(UNIFORM, 0.0, 1.0, -1.0, 0.25)
        self.assertAlmostEqual(w, 0.25)
        self.assertEqual(integrate_weight(UNIFORM, 0.0, 1.0, 2.0, 3.0), 0.0)

    def test_triangle_peak_at_center(self):
        self.assertAlmostEqual(TRIANGLE(0.5, 0.0, 1.0), 1.0)
        self.assertAlmostEqual(TRIANGLE(0.0, 0.0, 1.0), 0.0)


class TestIntegrator(unittest.TestCase):
    def test_static_exact_any_shutter(self):
        scene = StaticScene()
        times = [0.1, 0.3, 0.5, 0.7, 0.9]
        frames = scene.frames(times)
        ref = scene.frame_at(0.0)
        for weight in (UNIFORM, TRIANGLE):
            for dur in (0.2, 0.5, 1.0):
                res = integrate_frames(frames, times, 0.3, 0.3 + dur, weight)
                self.assertLess(np.abs(res.frame - ref).max(), 1e-6)

    def test_normalized_weights_sum_to_one(self):
        rng = np.random.default_rng(0)
        frames = rng.random((6, 2, 2, 4)).astype(np.float32)
        times = list((np.arange(6) + 0.5) / 6)  # cells tile [0, 1]
        res = integrate_frames(frames, times, 0.0, 1.0, TRIANGLE)
        self.assertAlmostEqual(
            res.weight_sum, total_weight(TRIANGLE, 0.0, 1.0), places=6
        )

    def test_missing_subframe_reduces_coverage(self):
        frames = np.ones((4, 1, 1, 4), dtype=np.float32)
        times = [0.1, 0.3, 0.7, 0.9]  # gap where t=0.5 is missing
        res = integrate_frames(
            frames, times, 0.0, 1.0, UNIFORM, nominal_dt=0.2
        )
        self.assertAlmostEqual(res.coverage, 0.8, places=6)

    def test_nonuniform_times_match_dense_reference(self):
        scene = MovingBarScene()
        times = [0.02, 0.1, 0.25, 0.5, 0.51, 0.7, 0.83, 0.97]
        res = integrate_frames(
            scene.frames(times), times, 0.0, 1.0, UNIFORM
        )
        ref = dense_reference(scene, 0.0, 1.0, UNIFORM)
        # piecewise-constant quadrature with 8 irregular samples
        self.assertLess(np.abs(res.frame - ref).max(), 0.1)

    def test_invalid_shutter_rejected(self):
        frames = np.ones((1, 1, 1, 4), dtype=np.float32)
        with self.assertRaises(ValueError):
            integrate_frames(frames, [0.5], 1.0, 0.0)


class TestScenes(unittest.TestCase):
    def test_bar_matches_analytic(self):
        scene = MovingBarScene()
        times = (np.arange(9) + 0.5) / 9
        for weight in (UNIFORM, TRIANGLE):
            res = integrate_frames(
                scene.frames(times), times, 0.0, 1.0, weight
            )
            ref = scene.analytic_reference(0.0, 1.0, weight)
            self.assertLess(np.abs(res.frame - ref).max(), 0.02)

    def test_occlusion_color_and_alpha(self):
        scene = OcclusionScene()
        times = (np.arange(9) + 0.5) / 9
        for weight in (UNIFORM, TRIANGLE):
            res = integrate_frames(
                scene.frames(times), times, 0.0, 1.0, weight
            )
            ref = scene.analytic_reference(0.0, 1.0, weight)
            err = np.abs(res.frame - ref)
            self.assertLess(err[..., :3].max(), 0.02)  # color
            self.assertLess(err[..., 3].max(), 0.02)  # coverage

    def test_streak_support_width(self):
        scene = MovingBarScene()
        times = (np.arange(9) + 0.5) / 9
        rows = slice(scene.row0, scene.row1)
        for weight in (UNIFORM, TRIANGLE):
            res = integrate_frames(
                scene.frames(times), times, 0.0, 1.0, weight
            )
            ref = scene.analytic_reference(0.0, 1.0, weight)
            got = measure_support_width(res.frame[rows, :, 0].max(axis=0))
            want = measure_support_width(ref[rows, :, 0].max(axis=0))
            self.assertAlmostEqual(got, want, delta=0.25)


class TestManifest(unittest.TestCase):
    def test_missing_detection(self):
        times = [0.1, 0.3, 0.5, 0.7, 0.9]
        subs = [SubFrame(time=t, path=f"{i}.npy") for i, t in enumerate(times)]
        frame = OutputFrame(
            index=3,
            t_open=0.0,
            t_close=1.0,
            subframes=[s for s in subs if s.time != 0.5],
            expected_times=times,
        )
        self.assertEqual(frame.missing_times(), [0.5])
        self.assertTrue(frame.has_missing)
        manifest = Manifest(frames=[frame])
        self.assertEqual(manifest.flagged_frames(), [3])

    def test_no_expected_times_means_no_flags(self):
        frame = OutputFrame(
            index=0, t_open=0.0, t_close=1.0,
            subframes=[SubFrame(time=0.5, path="a.npy")],
        )
        self.assertFalse(frame.has_missing)


if __name__ == "__main__":
    unittest.main()
