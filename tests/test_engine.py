import numpy as np
import pytest

from fdn_reverb.engine import FDNConfig, FDNReverb, RoomPreset
from fdn_reverb.metrics import (
    estimate_rt60,
    schroeder_edc_db,
    tail_metrics,
    transition_metrics,
)


def _impulse_response(rt60: float, sample_rate: int = 24_000) -> np.ndarray:
    duration = max(1.0, 1.7 * rt60)
    engine = FDNReverb(FDNConfig(sample_rate=sample_rate, rt60=rt60))
    excitation = np.zeros(int(duration * sample_rate))
    excitation[0] = 1.0
    return engine.process_block(excitation)


@pytest.mark.parametrize("rt60", [0.15, 0.6, 1.4])
def test_rt60_gain_mapping_tracks_requested_decay(rt60: float) -> None:
    sample_rate = 24_000
    response = _impulse_response(rt60, sample_rate)
    measured = estimate_rt60(response, sample_rate)
    assert measured == pytest.approx(rt60, rel=0.03)


def test_schroeder_curve_is_monotonic() -> None:
    response = _impulse_response(0.5)
    edc = schroeder_edc_db(response)
    assert edc[0] == pytest.approx(0.0, abs=1e-12)
    assert np.all(np.diff(edc) <= 1e-10)


def test_dynamic_rt60_changes_effective_feedback_matrix() -> None:
    engine = FDNReverb(
        FDNConfig(sample_rate=24_000, rt60=0.4, matrix_kind="householder")
    )
    before = np.linalg.norm(engine.effective_feedback_matrix, "fro")
    engine.transition_to_rt60(1.6, seconds=0.02)
    engine.process_block(np.zeros(int(0.03 * 24_000)))
    after = np.linalg.norm(engine.effective_feedback_matrix, "fro")
    assert engine.rt60 == pytest.approx(1.6, rel=1e-12)
    assert after > before * 1.4


def test_room_switch_has_no_click_and_continuous_local_energy() -> None:
    sample_rate = 48_000
    time = np.arange(int(2.6 * sample_rate)) / sample_rate
    source = 0.03 * (
        np.sin(2.0 * np.pi * 220.0 * time)
        + np.sin(2.0 * np.pi * 440.0 * time)
        + np.sin(2.0 * np.pi * 880.0 * time)
    )
    switch_seconds = 1.3
    ramp = 0.08
    switch_sample = int(switch_seconds * sample_rate)
    engine = FDNReverb(
        FDNConfig(
            sample_rate=sample_rate,
            rt60=0.55,
            matrix_kind="householder",
            transition_seconds=ramp,
        )
    )
    first = engine.process_block(source[:switch_sample])
    engine.transition_to_room(
        RoomPreset("hall", 1.8, "hadamard", seed=7),
        seconds=ramp,
    )
    second = engine.process_block(source[switch_sample:])
    metrics = transition_metrics(
        np.concatenate([first, second]),
        sample_rate,
        switch_seconds,
        ramp,
    )
    assert metrics.click_ratio < 1.25
    assert metrics.energy_step_db < 1.5
    assert metrics.energy_continuity_db < 0.5


def test_low_rt60_tail_ends_below_audible_level_without_fade() -> None:
    sample_rate = 48_000
    response = _impulse_response(0.15, sample_rate)
    metrics = tail_metrics(response, sample_rate)
    assert metrics["terminal_tail_db"] < -100.0
    assert metrics["final_step_db"] < -100.0

