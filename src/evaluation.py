from __future__ import annotations

from pathlib import Path

import numpy as np

from audio_utils import (
    FRAME_SECONDS,
    SAMPLE_RATE,
    align_delay,
    high_frequency_metrics,
    integrated_loudness,
    normalize_loudness,
    read_wav,
    rms,
    snr_db,
    write_wav,
)
from buffer_model import simulate_watermark_recovery
from denoisers import WEBRTC_DELAY, make_denoiser, process_python, process_webrtc

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_CONFIG = {
    "sample_rate_hz": SAMPLE_RATE,
    "frame_samples": 480,
    "frame_ms": 10.0,
    "target_lufs": -23.0,
    "loudness_tolerance_lu": 1.0,
    "snr_min_improvement_db": 0.0,
    "hf_split_hz": 8_000.0,
    "hf_min_band_change_db": -1.0,
    "hf_max_band_change_db": 1.0,
    "hf_max_relative_change_db": 1.0,
    "hf_min_log_spectral_correlation": 0.85,
    "hf_min_envelope_correlation": 0.75,
    "timing_repeats": 3,
    "max_rtf": 0.25,
    "buffer_target_frames": 5,
    "buffer_tolerance_frames": 1.0,
    "buffer_std_tolerance_frames": 0.5,
    "webrtc_level": "low",
    "webrtc_delay_samples": WEBRTC_DELAY,
}


def _process_algorithm(
    name: str,
    signal: np.ndarray,
    config: dict,
) -> tuple[np.ndarray, list[float], int]:
    if name == "webrtc":
        output, elapsed = process_webrtc(signal, level=config["webrtc_level"])
        return output, elapsed, config["webrtc_delay_samples"]
    denoiser = make_denoiser(name)
    output, elapsed = process_python(denoiser, signal)
    return output, elapsed, denoiser.delay


def _timing_summary(elapsed_runs: list[list[float]]) -> dict[str, float]:
    values = np.asarray([value for run in elapsed_runs for value in run], dtype=np.float64)
    frame_ns = FRAME_SECONDS * 1e9
    mean_ns = float(values.mean())
    rtf = mean_ns / frame_ns
    return {
        "measured_frames": int(values.size),
        "mean_us": mean_ns / 1e3,
        "median_us": float(np.median(values)) / 1e3,
        "p95_us": float(np.percentile(values, 95)) / 1e3,
        "max_us": float(values.max()) / 1e3,
        "rtf": float(rtf),
        "realtime_factor_x": float(1.0 / rtf),
    }


def _evaluate_algorithm(
    name: str,
    clean_speech: np.ndarray,
    noisy_speech: np.ndarray,
    music: np.ndarray,
    input_snr: float,
    config: dict,
    output_dir: Path,
) -> dict:
    elapsed_runs: list[list[float]] = []
    speech_outputs: list[np.ndarray] = []
    delay = 0
    for _ in range(config["timing_repeats"]):
        output, elapsed, delay = _process_algorithm(name, noisy_speech, config)
        elapsed_runs.append(elapsed)
        speech_outputs.append(output)

    reference_run = speech_outputs[0]
    max_run_delta = max(
        float(np.max(np.abs(reference_run - item))) for item in speech_outputs[1:]
    )
    aligned = align_delay(reference_run, delay)
    normalized, lufs_before, lufs_after = normalize_loudness(
        aligned, config["target_lufs"]
    )
    normalization_gain = rms(normalized) / max(rms(aligned), 1e-20)
    ideal_reference = clean_speech * normalization_gain
    output_snr = snr_db(ideal_reference, normalized)

    music_raw, _, music_delay = _process_algorithm(name, music, config)
    music_aligned = align_delay(music_raw, music_delay)
    music_normalized, music_before, music_after = normalize_loudness(
        music_aligned, config["target_lufs"]
    )
    hf = high_frequency_metrics(
        music, music_normalized, split_hz=config["hf_split_hz"]
    )

    write_wav(output_dir / f"{name}_speech.wav", normalized)
    write_wav(output_dir / f"{name}_music.wav", music_normalized)
    timing = _timing_summary(elapsed_runs)
    loudness_deviation = abs(lufs_after - config["target_lufs"])
    hf_pass = (
        hf["hf_band_energy_change_db"] >= config["hf_min_band_change_db"]
        and hf["hf_band_energy_change_db"] <= config["hf_max_band_change_db"]
        and abs(hf["hf_relative_energy_change_db"])
        <= config["hf_max_relative_change_db"]
        and hf["hf_log_spectral_correlation"]
        >= config["hf_min_log_spectral_correlation"]
        and hf["hf_envelope_correlation"] >= config["hf_min_envelope_correlation"]
    )
    snr_improvement = output_snr - input_snr
    return {
        "delay_samples": delay,
        "delay_ms": delay / SAMPLE_RATE * 1e3,
        "speech": {
            "input_snr_db": input_snr,
            "output_snr_db": output_snr,
            "snr_improvement_db": snr_improvement,
            "snr_pass": snr_improvement >= config["snr_min_improvement_db"],
            "loudness_before_lufs": lufs_before,
            "loudness_after_lufs": lufs_after,
            "normalization_gain_db": 20.0 * np.log10(max(normalization_gain, 1e-20)),
            "loudness_deviation_lu": loudness_deviation,
            "loudness_pass": loudness_deviation <= config["loudness_tolerance_lu"],
        },
        "music": {
            "loudness_before_lufs": music_before,
            "loudness_after_lufs": music_after,
            **hf,
            "high_frequency_pass": hf_pass,
        },
        "timing": timing,
        "timing_pass": timing["rtf"] < config["max_rtf"],
        "repeat_output_max_abs_delta": max_run_delta,
        "_speech_output": normalized,
        "_music_output": music_normalized,
    }


def evaluate(config: dict | None = None) -> dict:
    config = {**DEFAULT_CONFIG, **(config or {})}
    samples_dir = ROOT / "samples"
    clean_speech = read_wav(samples_dir / "clean_speech.wav")
    noisy_speech = read_wav(samples_dir / "noisy_speech.wav")
    music = read_wav(samples_dir / "clean_music.wav")
    input_snr = snr_db(clean_speech, noisy_speech)
    input_lufs = integrated_loudness(noisy_speech)

    output_dir = ROOT / "artifacts" / "audio"
    algorithms: dict[str, dict] = {}
    for name in ("spectral_subtraction", "mmse", "webrtc"):
        algorithms[name] = _evaluate_algorithm(
            name,
            clean_speech,
            noisy_speech,
            music,
            input_snr,
            config,
            output_dir,
        )

    buffer = simulate_watermark_recovery()
    buffer_pass = (
        abs(buffer["tail_mean_frames"] - config["buffer_target_frames"])
        <= config["buffer_tolerance_frames"]
        and buffer["tail_std_frames"] <= config["buffer_std_tolerance_frames"]
        and buffer["underruns"] == 0
    )
    checks = {
        "snr_improvement": all(
            item["speech"]["snr_pass"] for item in algorithms.values()
        ),
        "music_high_frequency": all(
            item["music"]["high_frequency_pass"] for item in algorithms.values()
        ),
        "loudness": all(
            item["speech"]["loudness_pass"] for item in algorithms.values()
        ),
        "realtime": all(item["timing_pass"] for item in algorithms.values()),
        "buffer_watermark": buffer_pass,
    }
    checks["all"] = all(checks.values())
    results = {
        "config": config,
        "input": {
            "snr_db": input_snr,
            "loudness_lufs": input_lufs,
            "loudness_deviation_lu": abs(input_lufs - config["target_lufs"]),
            "duration_s": len(noisy_speech) / SAMPLE_RATE,
        },
        "music": {
            "duration_s": len(music) / SAMPLE_RATE,
            "loudness_lufs": integrated_loudness(music),
        },
        "algorithms": algorithms,
        "buffer": {key: value for key, value in buffer.items() if key != "levels"},
        "checks": checks,
    }
    return results


def public_results(results: dict) -> dict:
    return {
        key: (
            {
                name: {k: v for k, v in item.items() if not k.startswith("_")}
                for name, item in value.items()
            }
            if key == "algorithms"
            else value
        )
        for key, value in results.items()
    }
