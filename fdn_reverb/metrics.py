"""Objective metrics for reverb quality and room transitions."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def _as_mono(signal: np.ndarray) -> np.ndarray:
    signal = np.asarray(signal, dtype=np.float64).reshape(-1)
    if signal.size == 0:
        raise ValueError("signal cannot be empty")
    return signal


def schroeder_edc_db(impulse_response: np.ndarray) -> np.ndarray:
    """Backward-integrated energy decay curve, normalized to 0 dB."""
    ir = _as_mono(impulse_response)
    energy = np.cumsum((ir * ir)[::-1])[::-1]
    if energy[0] <= 0.0:
        raise ValueError("impulse response has no energy")
    return 10.0 * np.log10(np.maximum(energy / energy[0], 1e-30))


def estimate_rt60(
    impulse_response: np.ndarray,
    sample_rate: int,
    decay_range_db: tuple[float, float] = (-5.0, -25.0),
) -> float:
    """Estimate RT60 by linear regression over a T20 decay range."""
    ir = _as_mono(impulse_response)
    edc = schroeder_edc_db(ir)
    times = np.arange(ir.size, dtype=np.float64) / sample_rate
    upper, lower = decay_range_db
    fit_region = (edc <= upper) & (edc >= lower)
    if np.count_nonzero(fit_region) < 16:
        raise ValueError("impulse response is too short for RT60 estimation")
    slope, _ = np.polyfit(times[fit_region], edc[fit_region], 1)
    if slope >= 0.0:
        raise ValueError("energy decay is not negative")
    return float(-60.0 / slope)


def tail_metrics(
    impulse_response: np.ndarray,
    sample_rate: int,
    terminal_window_seconds: float = 0.02,
) -> dict[str, float]:
    """Quantify whether an impulse response ends while its tail is still loud."""
    ir = _as_mono(impulse_response)
    peak = float(np.max(np.abs(ir)))
    if peak == 0.0:
        raise ValueError("impulse response has no energy")
    window = max(1, int(round(terminal_window_seconds * sample_rate)))
    terminal_rms = float(np.sqrt(np.mean(ir[-window:] ** 2)))
    terminal_db = 20.0 * np.log10(max(terminal_rms, 1e-30) / peak)
    final_step_db = 20.0 * np.log10(
        max(abs(float(ir[-1] - ir[-2])), 1e-30) / peak
    )
    edc = schroeder_edc_db(ir)
    return {
        "terminal_tail_db": terminal_db,
        "final_step_db": final_step_db,
        "terminal_edc_db": float(edc[-1]),
    }


def _frame_energy_db(
    signal: np.ndarray,
    sample_rate: int,
    frame_seconds: float,
    hop_seconds: float,
) -> tuple[np.ndarray, np.ndarray]:
    frame = max(1, int(round(frame_seconds * sample_rate)))
    hop = max(1, int(round(hop_seconds * sample_rate)))
    starts = np.arange(0, max(1, signal.size - frame + 1), hop)
    energies = np.empty(starts.size)
    for index, start in enumerate(starts):
        segment = signal[start : start + frame]
        energies[index] = np.mean(segment * segment) + 1e-30
    centers = (starts + frame / 2.0) / sample_rate
    return centers, 10.0 * np.log10(energies)


@dataclass(frozen=True)
class TransitionMetrics:
    click_ratio: float
    click_score_db: float
    energy_step_db: float
    energy_before_db: float
    energy_after_db: float
    energy_continuity_db: float


def transition_metrics(
    output: np.ndarray,
    sample_rate: int,
    switch_seconds: float,
    transition_seconds: float,
    frame_seconds: float = 0.01,
) -> TransitionMetrics:
    """Measure click and energy continuity around a scheduled room change."""
    signal = _as_mono(output)
    switch_sample = int(round(switch_seconds * sample_rate))
    transition_samples = max(1, int(round(transition_seconds * sample_rate)))
    guard = max(transition_samples, int(round(0.05 * sample_rate)))
    window_start = max(1, switch_sample - guard)
    window_stop = min(signal.size, switch_sample + transition_samples + guard)

    differences = np.abs(np.diff(signal))
    switch_diffs = differences[window_start:window_stop]
    reference_mask = np.ones(differences.size, dtype=bool)
    reference_mask[window_start:window_stop] = False
    reference = differences[reference_mask]
    reference_level = float(np.percentile(reference, 99.9)) if reference.size else 1e-30
    switch_level = float(np.max(switch_diffs)) if switch_diffs.size else 0.0
    click_ratio = switch_level / max(reference_level, 1e-30)

    centers, frame_db = _frame_energy_db(
        signal,
        sample_rate,
        frame_seconds=frame_seconds,
        hop_seconds=frame_seconds / 2.0,
    )
    active = (centers >= switch_seconds - transition_seconds)
    active &= centers <= switch_seconds + 2.0 * transition_seconds
    active_indices = np.flatnonzero(active)
    energy_step = (
        float(np.max(np.abs(np.diff(frame_db[active_indices]))))
        if active_indices.size >= 3
        else 0.0
    )
    before = centers < switch_seconds
    after = centers >= switch_seconds
    energy_before = float(np.median(frame_db[before][-5:]))
    energy_after = float(np.median(frame_db[after][:5]))
    return TransitionMetrics(
        click_ratio=click_ratio,
        click_score_db=20.0 * np.log10(max(click_ratio, 1e-30)),
        energy_step_db=energy_step,
        energy_before_db=energy_before,
        energy_after_db=energy_after,
        energy_continuity_db=abs(energy_after - energy_before),
    )


def _octave_band_edges(sample_rate: int) -> np.ndarray:
    centers = np.array([125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0])
    centers = centers[centers < sample_rate / 2.0]
    return np.concatenate([[centers[0] / np.sqrt(2.0)], centers * np.sqrt(2.0)])


def octave_band_levels_db(
    impulse_response: np.ndarray,
    sample_rate: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Return octave-band centers and normalized band energy."""
    ir = _as_mono(impulse_response)
    spectrum = np.fft.rfft(ir)
    power = np.abs(spectrum) ** 2
    frequencies = np.fft.rfftfreq(ir.size, 1.0 / sample_rate)
    edges = _octave_band_edges(sample_rate)
    centers = np.sqrt(edges[:-1] * edges[1:])
    levels = np.empty(centers.size)
    for index, (low, high) in enumerate(zip(edges[:-1], edges[1:])):
        band = (frequencies >= low) & (frequencies < high)
        levels[index] = np.sum(power[band])
    levels_db = 10.0 * np.log10(np.maximum(levels, 1e-30))
    levels_db -= np.max(levels_db)
    return centers, levels_db


def spectral_metrics(
    impulse_response: np.ndarray,
    sample_rate: int,
    f_min: float = 100.0,
    f_max: float | None = None,
) -> dict[str, float]:
    """Spectral flatness, octave spread, and centroid in an audible band."""
    ir = _as_mono(impulse_response)
    f_max = min(10_000.0, sample_rate / 2.0) if f_max is None else f_max
    spectrum = np.fft.rfft(ir)
    frequencies = np.fft.rfftfreq(ir.size, 1.0 / sample_rate)
    band = (frequencies >= f_min) & (frequencies <= f_max)
    power = np.maximum(np.abs(spectrum[band]) ** 2, 1e-30)
    flatness = float(np.exp(np.mean(np.log(power))) / np.mean(power))
    centroid = float(np.sum(frequencies[band] * power) / np.sum(power))
    _, octave_levels = octave_band_levels_db(ir, sample_rate)
    return {
        "spectral_flatness_db": 10.0 * np.log10(max(flatness, 1e-30)),
        "octave_spread_db": float(np.ptp(octave_levels)),
        "spectral_centroid_hz": centroid,
    }
