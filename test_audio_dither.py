"""Tests: shaping happens before quantization; THD and LF-residue checks."""
import numpy as np

from audio_metrics import band_rms_dbfs, residual, thd_db
from dither_quant import (
    SHAPER_PRESETS,
    quantize_16,
    quantize_24_to_16,
    quantize_with_trace,
    tpdf_dither,
)

FS = 44100
THD_THRESHOLD_DB = -80.0


def _tone(level_dbfs=-6.0, seconds=2.0, f0=997.0):
    t = np.arange(int(FS * seconds)) / FS
    return (10.0 ** (level_dbfs / 20.0)) * np.sin(2 * np.pi * f0 * t)


def test_shaping_precedes_quantization():
    """The quantizer input must be x - B*e_hist + dither (shaped BEFORE
    quantization): reconstruct v from past errors and check Q(v) == y."""
    rng = np.random.default_rng(0)
    x = _tone(seconds=0.1)
    taps = SHAPER_PRESETS["third_order"]
    d = tpdf_dither(x.shape[0], rng)
    y, v, e = quantize_with_trace(x, taps, d)
    # rebuild quantizer input independently from the recorded error history
    err_hist = np.zeros(len(taps))
    for i in range(x.shape[0]):
        shaped = x[i] - float(np.dot(taps, err_hist))
        assert abs(shaped + d[i] - v[i]) < 1e-15
        assert quantize_16(np.array([v[i]]))[0] == y[i]
        err_hist[1:] = err_hist[:-1]
        err_hist[0] = e[i]


def test_first_samples_have_no_feedback_yet():
    """With zero initial error history the first sample is quantized
    unshaped; feedback only enters afterwards (error-feedback topology)."""
    x = _tone(seconds=0.01)
    d = np.zeros(x.shape[0])
    for taps in (SHAPER_PRESETS["first_order"], SHAPER_PRESETS["third_order"]):
        y, v, _ = quantize_with_trace(x, taps, d)
        assert v[0] == x[0]


def test_shaped_thd_below_threshold():
    y = quantize_24_to_16(_tone(), dither=True, shaper="third_order", seed=3)
    assert thd_db(y, FS, 997.0) < THD_THRESHOLD_DB


def test_shaping_lowers_lf_residue():
    x = _tone(level_dbfs=-60.0)
    y_flat = quantize_24_to_16(x, dither=True, shaper="none", seed=5)
    y_shaped = quantize_24_to_16(x, dither=True, shaper="third_order", seed=5)
    lf_flat = band_rms_dbfs(residual(y_flat, x), FS, 20.0, 4000.0)
    lf_shaped = band_rms_dbfs(residual(y_shaped, x), FS, 20.0, 4000.0)
    assert lf_shaped < lf_flat - 15.0  # dither noise must not be pushed into LF
