from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass


@dataclass(frozen=True)
class NoiseParameters:
    seed: int
    mean: float
    stddev: float
    count: int = 40_000

    @property
    def variance(self) -> float:
        return self.stddev**2


@dataclass(frozen=True)
class NoiseStats:
    parameters: NoiseParameters
    sample_mean: float
    sample_variance: float
    mean_error: float
    variance_error: float
    mean_tolerance: float
    variance_tolerance: float
    accepted: bool


def gaussian_noise(parameters: NoiseParameters) -> list[float]:
    """Generate deterministic Gaussian samples for a seed and parameter set."""
    if parameters.stddev <= 0:
        raise ValueError("stddev must be positive")
    if parameters.count < 2:
        raise ValueError("count must be at least 2")
    rng = random.Random(parameters.seed)
    normal = statistics.NormalDist(parameters.mean, parameters.stddev)
    samples: list[float] = []
    for _ in range(parameters.count):
        # random() is in [0, 1); exclude both inverse-CDF endpoints.
        probability = min(max(rng.random(), 1e-15), 1.0 - 1e-15)
        samples.append(normal.inv_cdf(probability))
    return samples


def summarize(parameters: NoiseParameters) -> NoiseStats:
    samples = gaussian_noise(parameters)
    sample_mean = statistics.fmean(samples)
    sample_variance = statistics.variance(samples)
    mean_error = abs(sample_mean - parameters.mean)
    variance_error = abs(sample_variance - parameters.variance)
    mean_tolerance = 4.0 * parameters.stddev / math.sqrt(parameters.count)
    variance_tolerance = (
        4.0
        * parameters.variance
        * math.sqrt(2.0 / max(1, parameters.count - 1))
    )
    accepted = (
        mean_error <= mean_tolerance
        and variance_error <= variance_tolerance
    )
    return NoiseStats(
        parameters=parameters,
        sample_mean=sample_mean,
        sample_variance=sample_variance,
        mean_error=mean_error,
        variance_error=variance_error,
        mean_tolerance=mean_tolerance,
        variance_tolerance=variance_tolerance,
        accepted=accepted,
    )


DEFAULT_NOISE_PARAMETERS = (
    NoiseParameters(seed=11, mean=-2.0, stddev=0.5),
    NoiseParameters(seed=29, mean=0.0, stddev=1.0),
    NoiseParameters(seed=47, mean=3.5, stddev=2.0),
    NoiseParameters(seed=71, mean=10.0, stddev=0.25),
)
