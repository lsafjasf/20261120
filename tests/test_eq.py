"""Deterministic checks for biquad coefficients, response and switching."""

import math
import sys
import os

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from eq.biquad import (  # noqa: E402
    design_biquad,
    design_response,
    process_biquad,
)
from eq.measure import probed_response  # noqa: E402

FS = 48000


def test_unity_gain_peaking() -> None:
    c = design_biquad("peaking", 1000, FS, 0.0, 2.0)
    f = np.geomspace(30, FS / 2 * 0.99, 256)
    h = design_response([c], f, FS)
    assert np.max(np.abs(20 * np.log10(np.abs(h)))) < 1e-9


def test_peaking_extremum_at_fc() -> None:
    gain_db, fc, q = 6.0, 1000.0, 3.0
    c = design_biquad("peaking", fc, FS, gain_db, q)
    f = np.geomspace(100, 10000, 4000)
    mag = 20 * np.log10(np.abs(design_response([c], f, FS)))
    peak_f = f[int(np.argmax(mag))]
    assert abs(peak_f - fc) / fc < 0.005
    assert abs(mag.max() - gain_db) < 0.01


def test_dc_and_nyquist_shelves() -> None:
    low = design_biquad("low_shelf", 200, FS, 6.0, 1.0)
    high = design_biquad("high_shelf", 8000, FS, -6.0, 1.0)
    h_dc = design_response([low], np.array([1e-3]), FS)[0]
    h_nyq = design_response([high], np.array([FS / 2.0]), FS)[0]
    assert abs(20 * np.log10(abs(h_dc)) - 6.0) < 1e-9
    assert abs(20 * np.log10(abs(h_nyq)) - (-6.0)) < 1e-9


def test_tdf2_matches_lfilter() -> None:
    from scipy import signal
    rng = np.random.default_rng(0)
    x = rng.standard_normal(4096)
    c = design_biquad("peaking", 1200, FS, -8.0, 2.5)
    y_tdf, _ = process_biquad(x, c)
    y_ref = signal.lfilter([c.b0, c.b1, c.b2], [1, c.a1, c.a2], x)
    assert np.max(np.abs(y_tdf - y_ref)) < 1e-10


def test_probed_matches_design() -> None:
    c = design_biquad("peaking", 1000, FS, 6.0, 2.0)
    f = np.array([250.0, 1000.0, 4000.0])
    h_meas = probed_response([c], FS, f, tone_s=0.5, settle_s=0.15)
    h_design = design_response([c], f, FS)
    err_db = 20 * np.log10(np.abs(h_meas) / np.abs(h_design))
    assert np.max(np.abs(err_db)) < 0.02


def test_phase_at_nyquist_finite() -> None:
    bank = [
        design_biquad("low_shelf", 200, FS, 6.0, 1.0),
        design_biquad("peaking", 1000, FS, 6.0, 2.0),
        design_biquad("high_shelf", 8000, FS, -6.0, 1.0),
    ]
    h = design_response(bank, np.array([FS / 2.0]), FS)[0]
    assert math.isfinite(abs(h))


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
