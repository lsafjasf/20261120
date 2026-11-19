from __future__ import annotations

import numpy as np
import pytest

from src import fusion, metrics
from src.config import BANDS, CONFIG
from src.key_status import KEY_STATUS_ROWS


def test_identical_frames_score_perfectly() -> None:
    frame = np.random.default_rng(0).random((32, 48))
    assert metrics.ssim(frame, frame) == 1.0
    assert metrics.psnr_db(frame, frame) == 99.0
    energy, correlation = metrics.detail_components(frame, frame)
    assert (energy, correlation) == (1.0, 1.0)


def test_ssim_bounded_and_sensitive_to_degradation() -> None:
    rng = np.random.default_rng(1)
    reference = rng.random((32, 48))
    degraded = np.clip(reference + rng.normal(0, 0.05, reference.shape), 0, 1)
    score = metrics.ssim(reference, degraded)
    assert 0.0 < score < 1.0


def test_motion_fidelity_lost_motion_only() -> None:
    ref = np.array([0.10, 0.10, 0.10])
    cand = np.array([0.05, 0.10, 0.20])
    fidelity = np.clip(cand / ref, 0.0, 1.0)
    assert fidelity.tolist() == [0.5, 1.0, 1.0]


def test_motion_index_uses_reference_gain() -> None:
    deltas = np.array([0.011, 0.022])
    index = metrics.motion_index(deltas, CONFIG["motionScale"])
    gain = 1.0 / CONFIG["motionScale"]
    assert index[0] == pytest.approx(0.011 * gain)
    assert index[1] == pytest.approx(0.022 * gain)


def test_fusion_weights_sum_to_one_and_bounds() -> None:
    weights = CONFIG["weights"]
    assert sum(weights.values()) == 1.0
    assert fusion.fused_score(1.0, 1.0, 1.0) == 100.0
    assert fusion.fused_score(0.0, 0.0, 0.0) == 0.0


def test_bands_cover_full_scale() -> None:
    assert [name for name, _, _ in BANDS] == [
        "excellent",
        "good",
        "fair",
        "poor",
        "bad",
    ]
    assert fusion.band_for(93.0) == "excellent"
    assert fusion.band_for(68.0) == "good"
    assert fusion.band_for(52.0) == "fair"
    assert fusion.band_for(40.0) == "poor"
    assert fusion.band_for(20.0) == "bad"


def test_key_status_maps_to_playback_actions() -> None:
    by_status = {row["status"]: row for row in KEY_STATUS_ROWS}
    assert set(by_status) == {"usable", "expired", "output-restricted"}
    assert by_status["usable"]["pause"] is False
    assert by_status["usable"]["error"] is False
    assert by_status["expired"]["pause"] is True
    assert by_status["expired"]["error"] is False
    assert by_status["output-restricted"]["pause"] is True
    assert by_status["output-restricted"]["error"] is True
