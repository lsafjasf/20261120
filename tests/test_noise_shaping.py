import numpy as np

from fdn_reverb.noise_shaping import evaluate_noise_shaping, quantize


def test_noise_shaping_reduces_in_band_harmonic_distortion() -> None:
    sample_rate = 48_000
    time = np.arange(sample_rate) / sample_rate
    tone = 0.25 * np.sin(2.0 * np.pi * 1000.0 * time)
    result = evaluate_noise_shaping(tone, sample_rate, 1000.0, bits=12)
    assert result.thd_after_db < -80.0
    assert result.thd_after_db < result.thd_before_db - 20.0


def test_quantizer_uses_requested_bit_depth() -> None:
    samples = np.linspace(-0.9, 0.9, 1000)
    quantized = quantize(samples, bits=8)
    step = 2.0 / 2**8
    ratios = quantized / step
    assert np.max(np.abs(ratios - np.round(ratios))) < 1e-10
