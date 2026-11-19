import numpy as np

from agc_demo.agc import run_agc
from agc_demo.analysis import (
    detect_pumping,
    envelope_correlation_index,
    loudness_error_over_time,
    modulation_fidelity,
    noise_boost_in_pauses,
    rms_loudness_error_during_speech,
)
from agc_demo.signals import build_mixture


def test_eci_identity_is_high():
    mix = build_mixture()
    eci = envelope_correlation_index(mix.speech, mix.speech, mix.fs)
    assert eci > 0.95


def test_eci_degrades_with_distortion():
    mix = build_mixture()
    rng = np.random.default_rng(3)
    distorted = mix.speech + 0.3 * rng.standard_normal(mix.speech.size)
    clean_eci = envelope_correlation_index(mix.speech, mix.speech, mix.fs)
    bad_eci = envelope_correlation_index(mix.speech, distorted, mix.fs)
    assert bad_eci < clean_eci


def test_no_pumping_for_slew_limited_agc():
    mix = build_mixture()
    trace = run_agc(mix.mix, mix.fs, target_lufs=-23.0, release_s=0.2)
    report, _, _, _ = detect_pumping(trace, mix.mix, trace.output, mix.fs, -23.0)
    assert report.verdict == "NO_PUMPING"


def test_fast_ungated_detector_rides_envelope():
    mix = build_mixture()
    bad = run_agc(mix.mix, mix.fs, target_lufs=-23.0, frame_ms=40, hop_ms=20,
                  attack_s=0.005, release_s=0.02, max_rate_db_s=400.0)
    report, _, _, _ = detect_pumping(bad, mix.mix, bad.output, mix.fs, -23.0)
    assert report.jitter_index_db_per_100ms > 1.5
    assert report.verdict == "PUMPING_DETECTED"


def test_loudness_error_series_shape():
    mix = build_mixture()
    trace = run_agc(mix.mix, mix.fs, target_lufs=-23.0)
    times, err = loudness_error_over_time(trace.output, mix.fs, -23.0)
    assert times.shape == err.shape and times.size > 50


def test_release_time_intelligibility_tradeoff():
    mix = build_mixture()
    fast = run_agc(mix.mix, mix.fs, target_lufs=-23.0, release_s=0.05)
    slow = run_agc(mix.mix, mix.fs, target_lufs=-23.0, release_s=0.8)
    fast_err = rms_loudness_error_during_speech(
        fast.output, mix.fs, mix.speech_intervals, -23.0)
    slow_err = rms_loudness_error_during_speech(
        slow.output, mix.fs, mix.speech_intervals, -23.0)
    assert fast_err < slow_err  # fast release tracks speech level better
    fast_pause = noise_boost_in_pauses(fast.output, mix.fs,
                                       mix.speech_intervals)
    slow_pause = noise_boost_in_pauses(slow.output, mix.fs,
                                       mix.speech_intervals)
    assert fast_pause > slow_pause  # but lifts the pause floor more
    for tr in (fast, slow):
        fid = modulation_fidelity(mix.speech,
                                  mix.speech * 10 ** (tr.sample_gain_db / 20),
                                  mix.fs)
        assert 0.8 < fid <= 1.0
