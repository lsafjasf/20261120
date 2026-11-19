import numpy as np

from agc_demo.agc import run_agc, time_constant_to_db_per_s
from agc_demo.loudness import integrated_loudness
from agc_demo.signals import build_mixture


def test_slew_rate_is_limited():
    rng = np.random.default_rng(0)
    x = rng.standard_normal(16000) * 0.1
    x[8000:] *= 0.25  # abrupt 12 dB step
    trace = run_agc(x, 16000, target_lufs=-23.0, max_rate_db_s=25.0,
                    attack_s=0.005, release_s=0.5)
    dt = 0.1
    rates = np.abs(np.diff(trace.gain_db)) / dt
    assert np.all(rates <= 25.0 + 1e-9)


def test_gain_moves_toward_target():
    rng = np.random.default_rng(1)
    quiet = rng.standard_normal(16000) * 0.01
    trace = run_agc(quiet, 16000, target_lufs=-23.0)
    assert trace.gain_db[-1] > 10.0  # quiet input needs positive gain


def test_output_loudness_closer_to_target_than_input():
    mix = build_mixture()
    in_lufs = integrated_loudness(mix.mix, mix.fs)
    trace = run_agc(mix.mix, mix.fs, target_lufs=-23.0, release_s=0.2)
    out_lufs = integrated_loudness(trace.output, mix.fs)
    assert abs(out_lufs + 23.0) < abs(in_lufs + 23.0)


def test_time_constant_conversion():
    assert np.isclose(time_constant_to_db_per_s(1.0), 8.6858896, rtol=1e-5)
