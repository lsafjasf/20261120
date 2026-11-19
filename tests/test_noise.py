from __future__ import annotations

import unittest

from dash_events.noise import (
    DEFAULT_NOISE_PARAMETERS,
    NoiseParameters,
    gaussian_noise,
    summarize,
)


class NoiseTests(unittest.TestCase):
    def test_each_seed_matches_its_own_parameters(self) -> None:
        for parameters in DEFAULT_NOISE_PARAMETERS:
            with self.subTest(seed=parameters.seed):
                result = summarize(parameters)
                self.assertTrue(result.accepted)
                self.assertAlmostEqual(
                    result.sample_mean, parameters.mean, delta=result.mean_tolerance
                )
                self.assertAlmostEqual(
                    result.sample_variance,
                    parameters.variance,
                    delta=result.variance_tolerance,
                )

    def test_seed_is_reproducible_and_parameter_sensitive(self) -> None:
        first = NoiseParameters(seed=123, mean=-1.5, stddev=0.75, count=32)
        second = NoiseParameters(seed=124, mean=-1.5, stddev=0.75, count=32)
        scaled = NoiseParameters(seed=123, mean=-1.5, stddev=3.0, count=32)
        self.assertEqual(gaussian_noise(first), gaussian_noise(first))
        self.assertNotEqual(gaussian_noise(first), gaussian_noise(second))
        self.assertNotEqual(gaussian_noise(first), gaussian_noise(scaled))

    def test_invalid_noise_parameters_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            gaussian_noise(NoiseParameters(seed=1, mean=0.0, stddev=0.0))
        with self.assertRaises(ValueError):
            gaussian_noise(NoiseParameters(seed=1, mean=0.0, stddev=1.0, count=1))


if __name__ == "__main__":
    unittest.main()
