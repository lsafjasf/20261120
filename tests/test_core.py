import unittest

import numpy as np

from video_stabilizer.core import (
    build_output_matrix,
    compose_centered_similarity,
    decompose_centered_similarity,
    gaussian_smooth,
    safe_crop_linear_ratio,
)
from video_stabilizer.metrics import perspective_and_shear_error


class GeometryTests(unittest.TestCase):
    def test_centered_similarity_round_trip(self) -> None:
        matrix = compose_centered_similarity(3.5, -2.0, 0.08, 1.01, (320.0, 180.0))
        dx, dy, angle, scale = decompose_centered_similarity(matrix, (320.0, 180.0))
        self.assertAlmostEqual(dx, 3.5)
        self.assertAlmostEqual(dy, -2.0)
        self.assertAlmostEqual(angle, 0.08)
        self.assertAlmostEqual(scale, 1.01)

    def test_safe_crop_corner_maps_inside_source(self) -> None:
        width, height = 640, 360
        matrix = compose_centered_similarity(2.0, -1.0, 0.1, 1.0, (320.0, 180.0))
        ratio = safe_crop_linear_ratio(matrix, width, height, 2.0)
        output, _, _ = build_output_matrix(matrix, width, height, ratio)
        inverse = np.linalg.inv(output)
        corners = np.array(
            [[0, 0, 1], [width - 1, 0, 1], [width - 1, height - 1, 1], [0, height - 1, 1]]
        )
        source = (inverse @ corners.T).T[:, :2]
        self.assertTrue(np.all(source[:, 0] >= 1.9))
        self.assertTrue(np.all(source[:, 0] <= width - 2.9))
        self.assertTrue(np.all(source[:, 1] >= 1.9))
        self.assertTrue(np.all(source[:, 1] <= height - 2.9))

    def test_output_matrix_has_no_shear_or_perspective(self) -> None:
        matrix = compose_centered_similarity(4.0, 2.0, -0.12, 1.02, (320.0, 180.0))
        output, _, _ = build_output_matrix(matrix, 640, 360, 0.85)
        shear, perspective = perspective_and_shear_error(output[None, :, :])
        self.assertLess(shear, 1e-12)
        self.assertLess(perspective, 1e-12)

    def test_gaussian_smoothing_preserves_constant(self) -> None:
        values = np.full(50, 7.0)
        smoothed = gaussian_smooth(values, 4.0)
        np.testing.assert_allclose(smoothed, values)


if __name__ == "__main__":
    unittest.main()
