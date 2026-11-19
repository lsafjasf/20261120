import math
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import chroma_bench as bench


class ChromaBenchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reference = bench.build_reference_444(64, 48, 31)
        cls.source = bench.make_source_420(cls.reference)
        cls.reference_rgb = bench.ycbcr_to_rgb(
            cls.reference.y.astype(np.float64),
            cls.reference.cb.astype(np.float64),
            cls.reference.cr.astype(np.float64),
        )

    def test_source_has_half_resolution_chroma(self):
        self.assertEqual(self.source.y.shape, (48, 64))
        self.assertEqual(self.source.cb.shape, (24, 32))
        self.assertEqual(self.source.cr.shape, (24, 32))
        self.assertFalse(self.source.cb.flags.writeable)
        self.assertFalse(self.source.cr.flags.writeable)

    def test_all_methods_preserve_source_lattice(self):
        for method in bench.UPSAMPLERS:
            with self.subTest(method=method):
                reconstructed, _ = bench.reconstruct_444(self.source, method)
                np.testing.assert_array_equal(
                    reconstructed.cb[::2, ::2], self.source.cb
                )
                np.testing.assert_array_equal(
                    reconstructed.cr[::2, ::2], self.source.cr
                )

    def test_processing_does_not_modify_source(self):
        before = (self.source.cb.copy(), self.source.cr.copy())
        before_hash = bench.source_hash(self.source)
        for method in bench.UPSAMPLERS:
            bench.reconstruct_444(self.source, method)
        np.testing.assert_array_equal(self.source.cb, before[0])
        np.testing.assert_array_equal(self.source.cr, before[1])
        self.assertEqual(bench.source_hash(self.source), before_hash)

    def test_nearest_repeats_each_sample(self):
        restored = bench.upsample_nearest(self.source.cb)
        expected = np.repeat(np.repeat(self.source.cb, 2, axis=0), 2, axis=1)
        np.testing.assert_array_equal(restored, expected)

    def test_bilinear_odd_positions_are_halfway(self):
        line = np.array([[0, 20, 40, 60]], dtype=np.uint8)
        restored = bench.upsample_bilinear(line)
        np.testing.assert_allclose(
            restored[0], np.array([0, 10, 20, 30, 40, 50, 60, 60])
        )

    def test_lanczos_is_interpolating(self):
        line = np.array([[0, 20, 40, 60]], dtype=np.uint8)
        restored = bench.upsample_lanczos(line)
        np.testing.assert_allclose(restored[0, ::2], line[0])
        self.assertLess(restored[0, 1], 20.0)
        self.assertGreater(restored[0, 3], 10.0)

    def test_identical_images_have_infinite_psnr(self):
        self.assertTrue(
            math.isinf(bench.psnr_rgb(self.reference_rgb, self.reference_rgb))
        )

    def test_reference_has_no_bleed_or_ringing(self):
        metrics = bench.color_bleed_metrics(
            self.reference,
            bench.Frame444(
                self.reference.y,
                self.reference.cb.astype(np.float64),
                self.reference.cr.astype(np.float64),
            ),
            self.reference_rgb,
            edge_x=31,
            radius=4,
        )
        self.assertEqual(metrics["combined"]["ringing_energy_codes"], 0.0)
        self.assertEqual(metrics["combined"]["mean_leak_percent"], 0.0)
        self.assertEqual(metrics["combined"]["max_ringing_overshoot_codes"], 0.0)

    def test_lanczos_rings_where_linear_methods_do_not(self):
        ringing = {}
        for method in bench.UPSAMPLERS:
            reconstructed, rgb = bench.reconstruct_444(self.source, method)
            metrics = bench.color_bleed_metrics(
                self.reference,
                reconstructed,
                rgb,
                edge_x=31,
                radius=4,
            )
            ringing[method] = metrics["combined"]["ringing_energy_codes"]
        self.assertEqual(ringing["nearest"], 0.0)
        self.assertEqual(ringing["bilinear"], 0.0)
        self.assertGreater(ringing["lanczos3"], 0.0)


if __name__ == "__main__":
    unittest.main()
