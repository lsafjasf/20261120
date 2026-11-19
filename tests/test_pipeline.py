"""Sanity tests for the keyword-spotting and AEC components."""

import numpy as np

from kws import features, synth
from kws.aec import NLMS, block_erle, make_echo_path
from kws.dtw import KeywordDetector, KeywordSpotter, dtw_distance


def test_dtw_symmetry_and_self_distance():
    rng = np.random.default_rng(1)
    a = rng.standard_normal((20, 10)).astype(np.float32)
    b = rng.standard_normal((24, 10)).astype(np.float32)
    assert abs(dtw_distance(a, b) - dtw_distance(b, a)) < 1e-5
    assert dtw_distance(a, a) < 1e-6


def test_dtw_band_handles_length_mismatch():
    rng = np.random.default_rng(2)
    a = rng.standard_normal((10, 8)).astype(np.float32)
    b = rng.standard_normal((30, 8)).astype(np.float32)
    value = dtw_distance(a, b, band=0.15)
    assert np.isfinite(value) and value > 0


def test_feature_shapes_and_finite():
    rng = np.random.default_rng(3)
    for kind in (0, 1):
        sig = synth.generate_keyword(kind, rng)
        feat = features.extract_features(sig, synth.FS)
        assert feat.ndim == 2
        assert feat.shape[1] == 3 * features.N_MELS
        assert np.isfinite(feat).all()


def test_noise_and_speed_keep_features_valid():
    rng = np.random.default_rng(4)
    sig = synth.generate_keyword(0, rng)
    noisy = synth.add_noise_at_snr(
        sig, synth.generate_noise_segment(rng), 0.0, rng)
    shifted = synth.time_stretch(sig, 0.9)
    for x in (noisy, shifted):
        feat = features.extract_features(x, synth.FS)
        assert len(feat) >= 5 and np.isfinite(feat).all()


def test_detector_prefers_own_keyword():
    rng = np.random.default_rng(5)
    own = [features.extract_features(synth.generate_keyword(0, rng), synth.FS)
           for _ in range(12)]
    other = [features.extract_features(synth.generate_keyword(1, rng), synth.FS)
             for _ in range(12)]
    det = KeywordDetector(0, "A", band=0.3)
    det.enroll(own, n_templates=3)
    own_dist = np.mean([det.raw_distance(f) for f in own])
    other_dist = np.mean([det.raw_distance(f) for f in other])
    assert own_dist < other_dist


def test_spotter_decides_correct_keyword():
    rng = np.random.default_rng(6)
    detectors = {}
    for kind in (0, 1):
        feats = [features.extract_features(
            synth.generate_keyword(kind, rng), synth.FS) for _ in range(12)]
        det = KeywordDetector(kind, str(kind), band=0.3)
        det.enroll(feats, n_templates=3)
        det.calibrate(np.array([5.0, 6.0]))
        det.set_threshold_by_distance(1e6)
        detectors[kind] = det
    spotter = KeywordSpotter(detectors, margin=0.0)
    for kind in (0, 1):
        feat = features.extract_features(
            synth.generate_keyword(kind, rng), synth.FS)
        label, _ = spotter.decide(feat)
        assert label == kind


def test_nlms_converges_on_static_echo_path():
    fs = 8000
    rng = np.random.default_rng(7)
    n = 4 * fs
    reference = rng.standard_normal(n)
    ir = make_echo_path(fs, seed=11)
    desired = np.convolve(reference, ir)[:n]
    nlms = NLMS(taps=320, mu=1.0)
    error = np.array([nlms.step(reference[i], desired[i])[0]
                      for i in range(n)])
    erle = block_erle(desired, error, fs)
    assert erle[-10:].mean() > 15.0
