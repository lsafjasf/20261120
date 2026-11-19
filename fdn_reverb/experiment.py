"""Reproducible reverb experiments and artifact generation."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import FuncFormatter
from scipy.io import wavfile

from .engine import FDNConfig, FDNReverb, RoomPreset
from .matrices import build_feedback_matrix, orthogonality_error
from .metrics import (
    estimate_rt60,
    octave_band_levels_db,
    schroeder_edc_db,
    spectral_metrics,
    tail_metrics,
    transition_metrics,
)
from .noise_shaping import evaluate_noise_shaping, welch_spectrum_db


SAMPLE_RATE = 48_000
MATRIX_CASES = (
    ("Hadamard", "hadamard"),
    ("Householder", "householder"),
    ("QR", "qr"),
    ("Non-orthogonal", "nonorthogonal"),
)
THRESHOLDS = {
    "rt60_relative_error": 0.03,
    "switch_click_ratio": 1.25,
    "switch_energy_step_db": 1.5,
    "switch_energy_continuity_db": 0.5,
    "low_rt60_terminal_tail_db": -100.0,
    "low_rt60_final_step_db": -100.0,
    "noise_shaped_thd_db": -80.0,
}


def _json_float(value: float) -> float:
    return float(np.round(float(value), 9))


def render_impulse_response(
    config: FDNConfig,
    duration_seconds: float | None = None,
) -> tuple[FDNReverb, np.ndarray]:
    duration = (
        max(1.0, 1.7 * config.rt60)
        if duration_seconds is None
        else duration_seconds
    )
    length = int(round(duration * config.sample_rate))
    excitation = np.zeros(length, dtype=np.float64)
    excitation[0] = 1.0
    engine = FDNReverb(config)
    return engine, engine.process_block(excitation)


def _tone_input(seconds: float, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    time = np.arange(int(round(seconds * sample_rate)), dtype=np.float64) / sample_rate
    return 0.03 * (
        np.sin(2.0 * np.pi * 220.0 * time)
        + np.sin(2.0 * np.pi * 440.0 * time)
        + np.sin(2.0 * np.pi * 880.0 * time)
    )


def run_switch_experiment(transition_seconds: float = 0.08) -> dict[str, object]:
    input_signal = _tone_input(3.0)
    switch_seconds = 1.5
    switch_sample = int(round(switch_seconds * SAMPLE_RATE))
    start_room = RoomPreset("studio", 0.55, "householder", seed=7)
    target_room = RoomPreset("hall", 1.80, "hadamard", seed=7)

    def simulate(ramp: float) -> np.ndarray:
        config = FDNConfig(
            sample_rate=SAMPLE_RATE,
            rt60=start_room.rt60,
            matrix_kind=start_room.matrix_kind,
            matrix_seed=start_room.seed,
            transition_seconds=ramp,
        )
        engine = FDNReverb(config)
        first = engine.process_block(input_signal[:switch_sample])
        engine.transition_to_room(target_room, seconds=ramp)
        second = engine.process_block(input_signal[switch_sample:])
        return np.concatenate([first, second])

    smooth = simulate(transition_seconds)
    abrupt = simulate(0.0)
    smooth_metrics = transition_metrics(
        smooth,
        SAMPLE_RATE,
        switch_seconds,
        transition_seconds,
    )
    abrupt_metrics = transition_metrics(
        abrupt,
        SAMPLE_RATE,
        switch_seconds,
        1.0 / SAMPLE_RATE,
    )
    passed = (
        smooth_metrics.click_ratio < THRESHOLDS["switch_click_ratio"]
        and smooth_metrics.energy_step_db < THRESHOLDS["switch_energy_step_db"]
        and smooth_metrics.energy_continuity_db
        < THRESHOLDS["switch_energy_continuity_db"]
    )
    return {
        "signal": smooth,
        "switch_seconds": switch_seconds,
        "transition_seconds": transition_seconds,
        "start_room": start_room,
        "target_room": target_room,
        "smooth": smooth_metrics,
        "abrupt": abrupt_metrics,
        "passed": passed,
    }


def run_rt60_transition() -> dict[str, object]:
    engine = FDNReverb(
        FDNConfig(sample_rate=SAMPLE_RATE, rt60=0.35, matrix_kind="householder")
    )
    duration = 1.2
    length = int(duration * SAMPLE_RATE)
    switch_sample = int(0.5 * SAMPLE_RATE)
    signal = np.zeros(length, dtype=np.float64)
    signal[0] = 1.0
    first = engine.process_block(signal[:switch_sample])
    target_rt60 = 1.8
    engine.transition_to_rt60(target_rt60, seconds=0.12)
    second = np.empty(length - switch_sample)
    rt60_trace = np.empty_like(second)
    matrix_norm_trace = np.empty_like(second)
    for index, sample in enumerate(signal[switch_sample:]):
        second[index] = engine.process_sample(float(sample))
        rt60_trace[index] = engine.rt60
        matrix_norm_trace[index] = np.linalg.norm(
            engine.effective_feedback_matrix,
            "fro",
        )
    return {
        "input": signal,
        "output": np.concatenate([first, second]),
        "switch_seconds": switch_sample / SAMPLE_RATE,
        "target_rt60": target_rt60,
        "rt60_trace": rt60_trace,
        "matrix_norm_trace": matrix_norm_trace,
    }


def run_main_experiment() -> dict[str, object]:
    config = FDNConfig(
        sample_rate=SAMPLE_RATE,
        rt60=1.2,
        matrix_kind="householder",
        matrix_seed=7,
        transition_seconds=0.08,
    )
    engine, response = render_impulse_response(config)
    edc = schroeder_edc_db(response)
    measured_rt60 = estimate_rt60(response, SAMPLE_RATE)
    relative_error = abs(measured_rt60 - config.rt60) / config.rt60
    return {
        "impulse_response": response,
        "edc": edc,
        "target_rt60": config.rt60,
        "measured_rt60": measured_rt60,
        "relative_error": relative_error,
        "tail": tail_metrics(response, SAMPLE_RATE),
        "spectral": spectral_metrics(response, SAMPLE_RATE),
        "effective_feedback_norm": float(
            np.linalg.norm(engine.effective_feedback_matrix, "fro")
        ),
        "passed": relative_error < THRESHOLDS["rt60_relative_error"],
    }


def run_matrix_experiment() -> dict[str, object]:
    results: dict[str, object] = {}
    reference_levels: np.ndarray | None = None
    for label, kind in MATRIX_CASES:
        config = FDNConfig(
            sample_rate=SAMPLE_RATE,
            rt60=1.2,
            matrix_kind=kind,
            matrix_seed=7,
        )
        engine, response = render_impulse_response(config)
        centers, levels = octave_band_levels_db(response, SAMPLE_RATE)
        if reference_levels is None:
            reference_levels = levels
        results[kind] = {
            "label": label,
            "matrix": engine.matrix,
            "orthogonality_error": orthogonality_error(engine.matrix),
            "condition_number": float(np.linalg.cond(engine.matrix)),
            "rt60": estimate_rt60(response, SAMPLE_RATE),
            "spectral": spectral_metrics(response, SAMPLE_RATE),
            "octave_centers": centers,
            "octave_levels_db": levels,
            "tonal_deviation_db": float(
                np.sqrt(np.mean((levels - reference_levels) ** 2))
            ),
        }
    return results


def run_low_rt60_experiment() -> dict[str, object]:
    target_rt60 = 0.15
    config = FDNConfig(
        sample_rate=SAMPLE_RATE,
        rt60=target_rt60,
        matrix_kind="householder",
    )
    _, response = render_impulse_response(config, duration_seconds=1.0)
    metrics = tail_metrics(response, SAMPLE_RATE)
    passed = (
        metrics["terminal_tail_db"] < THRESHOLDS["low_rt60_terminal_tail_db"]
        and metrics["final_step_db"] < THRESHOLDS["low_rt60_final_step_db"]
    )
    return {
        "impulse_response": response,
        "target_rt60": target_rt60,
        "measured_rt60": estimate_rt60(response, SAMPLE_RATE),
        "duration_seconds": 1.0,
        "metrics": metrics,
        "passed": passed,
    }


def run_noise_shaping_experiment() -> dict[str, object]:
    duration = 1.0
    fundamental = 1000.0
    time = np.arange(int(duration * SAMPLE_RATE)) / SAMPLE_RATE
    tone = 0.25 * np.sin(2.0 * np.pi * fundamental * time)
    result = evaluate_noise_shaping(
        tone,
        SAMPLE_RATE,
        fundamental,
        bits=12,
    )
    before = np.round(tone / (2.0 / 2**12)) * (2.0 / 2**12)
    before_frequencies, before_spectrum = welch_spectrum_db(
        before,
        SAMPLE_RATE,
        fundamental,
    )
    after_frequencies, after_spectrum = welch_spectrum_db(
        result.samples,
        SAMPLE_RATE,
        fundamental,
    )
    passed = result.thd_after_db < THRESHOLDS["noise_shaped_thd_db"]
    return {
        "bits": result.bits,
        "fundamental_hz": fundamental,
        "thd_before_db": result.thd_before_db,
        "thd_after_db": result.thd_after_db,
        "before_frequencies": before_frequencies,
        "before_spectrum_db": before_spectrum,
        "after_frequencies": after_frequencies,
        "after_spectrum_db": after_spectrum,
        "passed": passed,
    }


def _finish_figure(figure: plt.Figure, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.tight_layout()
    figure.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(figure)


def plot_main_response(result: dict[str, object], path: Path) -> None:
    response = np.asarray(result["impulse_response"])
    edc = np.asarray(result["edc"])
    time = np.arange(response.size) / SAMPLE_RATE
    figure, axes = plt.subplots(2, 1, figsize=(9, 6), sharex=True)
    axes[0].plot(time, response, color="#26547c", linewidth=0.8)
    axes[0].set_ylabel("Wet IR amplitude")
    axes[0].set_title(
        f"FDN impulse response: {result['measured_rt60']:.3f} s measured RT60"
    )
    axes[1].plot(time, edc, color="#ef476f", linewidth=1.4)
    axes[1].axhline(-60.0, color="black", linestyle="--", linewidth=0.8)
    axes[1].set_ylim(-80.0, 2.0)
    axes[1].set_ylabel("Schroeder EDC (dB)")
    axes[1].set_xlabel("Time (s)")
    for axis in axes:
        axis.grid(alpha=0.25)
    _finish_figure(figure, path)


def plot_matrix_comparison(results: dict[str, object], path: Path) -> None:
    figure, axes = plt.subplots(1, 2, figsize=(11, 4.5))
    colors = ("#1d3557", "#2a9d8f", "#e9c46a", "#e76f51")
    for color, (kind, raw) in zip(colors, results.items()):
        item = raw
        axes[0].plot(
            item["octave_centers"],
            item["octave_levels_db"],
            marker="o",
            linewidth=1.7,
            color=color,
            label=item["label"],
        )
    axes[0].set_xscale("log")
    octave_ticks = [125, 250, 500, 1000, 2000, 4000, 8000]
    axes[0].set_xticks(octave_ticks)
    axes[0].set_xticklabels([f"{value:g}" for value in octave_ticks])
    axes[0].xaxis.set_minor_formatter(FuncFormatter(lambda *_: ""))
    axes[0].set_xlabel("Octave-band center (Hz)")
    axes[0].set_ylabel("Band energy (dB, max-normalized)")
    axes[0].set_title("Feedback matrix tonal response")
    axes[0].grid(alpha=0.25, which="both")
    axes[0].legend(fontsize=8)

    labels = [item["label"] for item in results.values()]
    errors = [max(item["orthogonality_error"], 1e-18) for item in results.values()]
    positions = np.arange(len(labels))
    axes[1].bar(positions, errors, color=colors)
    axes[1].set_yscale("log")
    axes[1].set_xticks(positions, labels, rotation=15)
    axes[1].set_ylabel(r"$\Vert M^T M-I\Vert_F/\sqrt{N}$")
    axes[1].set_title("Orthogonality error (lower is more orthogonal)")
    axes[1].grid(axis="y", alpha=0.25)
    for position, value in zip(positions, errors):
        axes[1].text(
            position,
            value * 1.4,
            f"{value:.1e}",
            ha="center",
            fontsize=8,
        )
    _finish_figure(figure, path)


def plot_switch(result: dict[str, object], path: Path) -> None:
    signal = np.asarray(result["signal"])
    switch_time = float(result["switch_seconds"])
    time = np.arange(signal.size) / SAMPLE_RATE
    smooth = result["smooth"]
    abrupt = result["abrupt"]

    figure, axes = plt.subplots(2, 1, figsize=(9, 6))
    zoom = (time >= switch_time - 0.12) & (time <= switch_time + 0.18)
    axes[0].plot(time[zoom], signal[zoom], color="#264653", linewidth=1.0)
    axes[0].axvline(switch_time, color="#e76f51", linestyle="--", label="switch")
    axes[0].set_xlabel("Time (s)")
    axes[0].set_ylabel("Wet output")
    axes[0].set_title(
        "Room change (80 ms): "
        f"click ratio {smooth.click_ratio:.3f}x vs abrupt {abrupt.click_ratio:.3f}x"
    )
    axes[0].legend()
    axes[0].grid(alpha=0.25)

    frame = int(0.01 * SAMPLE_RATE)
    hop = frame // 2
    starts = np.arange(0, signal.size - frame + 1, hop)
    centers = (starts + frame / 2.0) / SAMPLE_RATE
    energy_db = 10.0 * np.log10(
        np.array([np.mean(signal[start : start + frame] ** 2) for start in starts])
        + 1e-30
    )
    energy_zoom = (centers >= switch_time - 0.35) & (centers <= switch_time + 0.35)
    axes[1].plot(centers[energy_zoom], energy_db[energy_zoom], color="#2a9d8f")
    axes[1].axvline(switch_time, color="#e76f51", linestyle="--")
    axes[1].set_xlabel("Time (s)")
    axes[1].set_ylabel("10 ms frame energy (dB)")
    axes[1].set_title(
        "Energy continuity: "
        f"adjacent step {smooth.energy_step_db:.2f} dB, "
        f"before/after {smooth.energy_continuity_db:.2f} dB"
    )
    axes[1].grid(alpha=0.25)
    _finish_figure(figure, path)


def plot_rt60_transition(result: dict[str, object], path: Path) -> None:
    switch_time = float(result["switch_seconds"])
    trace = np.asarray(result["rt60_trace"])
    matrix_norm = np.asarray(result["matrix_norm_trace"])
    time = switch_time + np.arange(trace.size) / SAMPLE_RATE

    figure, axes = plt.subplots(2, 1, figsize=(9, 5.5), sharex=True)
    axes[0].plot(time, trace, color="#26547c", linewidth=1.7)
    axes[0].axhline(
        float(result["target_rt60"]),
        color="#e76f51",
        linestyle="--",
        label="target RT60",
    )
    axes[0].set_ylabel("RT60 (s)")
    axes[0].set_title("Dynamic RT60 retunes the effective feedback matrix")
    axes[0].legend()
    axes[0].grid(alpha=0.25)
    axes[1].plot(
        time,
        matrix_norm / matrix_norm[0],
        color="#2a9d8f",
        linewidth=1.7,
    )
    axes[1].set_xlabel("Time (s)")
    axes[1].set_ylabel("Relative feedback norm")
    axes[1].grid(alpha=0.25)
    _finish_figure(figure, path)


def plot_low_rt60_tail(result: dict[str, object], path: Path) -> None:
    response = np.asarray(result["impulse_response"])
    time = np.arange(response.size) / SAMPLE_RATE
    absolute = np.maximum(np.abs(response), 1e-24)
    figure, axes = plt.subplots(2, 1, figsize=(9, 5.5), sharex=True)
    axes[0].semilogy(time, absolute, color="#264653", linewidth=0.9)
    axes[0].set_ylabel("|IR|")
    axes[0].set_title(
        f"Low RT60 = {result['target_rt60']:.2f} s, "
        f"tail end = {result['metrics']['terminal_tail_db']:.1f} dB"
    )
    axes[0].grid(alpha=0.25, which="both")
    edc = schroeder_edc_db(response)
    axes[1].plot(time, edc, color="#ef476f", linewidth=1.4)
    axes[1].axhline(
        THRESHOLDS["low_rt60_terminal_tail_db"],
        color="black",
        linestyle="--",
        linewidth=0.8,
        label="cutoff acceptance",
    )
    axes[1].set_xlabel("Time (s)")
    axes[1].set_ylabel("EDC (dB)")
    axes[1].set_ylim(-320.0, 2.0)
    axes[1].legend()
    axes[1].grid(alpha=0.25)
    _finish_figure(figure, path)


def plot_noise_shaping(result: dict[str, object], path: Path) -> None:
    before_f = np.asarray(result["before_frequencies"])
    after_f = np.asarray(result["after_frequencies"])
    before = np.asarray(result["before_spectrum_db"])
    after = np.asarray(result["after_spectrum_db"])
    figure, axis = plt.subplots(figsize=(9, 4.8))
    axis.plot(before_f, before, color="#457b9d", linewidth=1.0, label="12-bit, no shaping")
    axis.plot(after_f, after, color="#e63946", linewidth=1.0, label="12-bit, shaped")
    axis.set_xscale("log")
    axis.set_xlim(100.0, 24_000.0)
    axis.set_ylim(-170.0, 5.0)
    axis.set_xlabel("Frequency (Hz)")
    axis.set_ylabel("Level re. fundamental (dB)")
    axis.set_title(
        f"Noise shaping: THD {result['thd_before_db']:.1f} -> "
        f"{result['thd_after_db']:.1f} dB"
    )
    axis.grid(alpha=0.25, which="both")
    axis.legend()
    _finish_figure(figure, path)


def _write_float_wav(path: Path, signal: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    samples = np.asarray(signal, dtype=np.float64)
    peak = float(np.max(np.abs(samples)))
    if peak > 0.0:
        samples = 0.9 * samples / peak
    wavfile.write(path, SAMPLE_RATE, samples.astype(np.float32))


def _transition_to_dict(metrics: object) -> dict[str, float]:
    return {
        "click_ratio": _json_float(metrics.click_ratio),
        "click_score_db": _json_float(metrics.click_score_db),
        "energy_step_db": _json_float(metrics.energy_step_db),
        "energy_before_db": _json_float(metrics.energy_before_db),
        "energy_after_db": _json_float(metrics.energy_after_db),
        "energy_continuity_db": _json_float(metrics.energy_continuity_db),
    }


def _build_results_json(
    main: dict[str, object],
    matrices: dict[str, object],
    switch: dict[str, object],
    low_rt60: dict[str, object],
    noise: dict[str, object],
) -> dict[str, object]:
    matrix_rows = {
        kind: {
            "label": item["label"],
            "orthogonality_error": _json_float(item["orthogonality_error"]),
            "condition_number": _json_float(item["condition_number"]),
            "measured_rt60": _json_float(item["rt60"]),
            "spectral_flatness_db": _json_float(
                item["spectral"]["spectral_flatness_db"]
            ),
            "octave_spread_db": _json_float(item["spectral"]["octave_spread_db"]),
            "spectral_centroid_hz": _json_float(
                item["spectral"]["spectral_centroid_hz"]
            ),
            "tonal_deviation_db": _json_float(item["tonal_deviation_db"]),
        }
        for kind, item in matrices.items()
    }
    checks = {
        "main_rt60_accuracy": bool(main["passed"]),
        "room_switch_click_free": bool(
            switch["smooth"].click_ratio < THRESHOLDS["switch_click_ratio"]
        ),
        "room_switch_energy_continuous": bool(
            switch["smooth"].energy_step_db
            < THRESHOLDS["switch_energy_step_db"]
            and switch["smooth"].energy_continuity_db
            < THRESHOLDS["switch_energy_continuity_db"]
        ),
        "low_rt60_tail_not_truncated": bool(low_rt60["passed"]),
        "noise_shaped_thd": bool(noise["passed"]),
    }
    return {
        "sample_rate": SAMPLE_RATE,
        "thresholds": THRESHOLDS,
        "main_impulse_response": {
            "target_rt60": _json_float(main["target_rt60"]),
            "measured_rt60": _json_float(main["measured_rt60"]),
            "relative_error": _json_float(main["relative_error"]),
            "effective_feedback_norm": _json_float(
                main["effective_feedback_norm"]
            ),
            "tail": {
                key: _json_float(value)
                for key, value in main["tail"].items()
            },
            "spectral": {
                key: _json_float(value)
                for key, value in main["spectral"].items()
            },
        },
        "feedback_matrices": matrix_rows,
        "room_switch": {
            "switch_seconds": _json_float(switch["switch_seconds"]),
            "transition_seconds": _json_float(switch["transition_seconds"]),
            "from_room": switch["start_room"].name,
            "to_room": switch["target_room"].name,
            "smooth": _transition_to_dict(switch["smooth"]),
            "abrupt_reference": _transition_to_dict(switch["abrupt"]),
        },
        "low_rt60": {
            "target_rt60": _json_float(low_rt60["target_rt60"]),
            "measured_rt60": _json_float(low_rt60["measured_rt60"]),
            "duration_seconds": _json_float(low_rt60["duration_seconds"]),
            "metrics": {
                key: _json_float(value)
                for key, value in low_rt60["metrics"].items()
            },
        },
        "noise_shaping": {
            "bits": noise["bits"],
            "fundamental_hz": _json_float(noise["fundamental_hz"]),
            "thd_before_db": _json_float(noise["thd_before_db"]),
            "thd_after_db": _json_float(noise["thd_after_db"]),
        },
        "checks": checks,
        "all_required_checks_passed": all(checks.values()),
        "figures": [
            "figures/main_impulse_response.png",
            "figures/matrix_orthogonality.png",
            "figures/room_switch.png",
            "figures/rt60_dynamic_update.png",
            "figures/low_rt60_tail.png",
            "figures/noise_shaping_spectrum.png",
        ],
        "audio": [
            "audio/main_impulse_response.wav",
            "audio/room_switch.wav",
            "audio/low_rt60_tail.wav",
            "audio/rt60_dynamic_update.wav",
        ],
    }


def _matrix_table(matrices: dict[str, object]) -> str:
    lines = [
        "| 矩阵 | 正交性误差 | 条件数 | 实测 RT60 (s) | 倍频程起伏 (dB) | 相对音色偏移 (dB) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for item in matrices.values():
        lines.append(
            f"| {item['label']} | {item['orthogonality_error']:.2e} | "
            f"{item['condition_number']:.2f} | {item['rt60']:.3f} | "
            f"{item['spectral']['octave_spread_db']:.2f} | "
            f"{item['tonal_deviation_db']:.2f} |"
        )
    return "\n".join(lines)


def _write_report(
    path: Path,
    main: dict[str, object],
    matrices: dict[str, object],
    switch: dict[str, object],
    low_rt60: dict[str, object],
    noise: dict[str, object],
) -> None:
    smooth = switch["smooth"]
    abrupt = switch["abrupt"]
    low_metrics = low_rt60["metrics"]
    checks = {
        "RT60 相对误差 < 3%": main["passed"],
        "切换咔哒指标 < 1.25x": smooth.click_ratio
        < THRESHOLDS["switch_click_ratio"],
        "切换相邻帧能量阶跃 < 1.5 dB": smooth.energy_step_db
        < THRESHOLDS["switch_energy_step_db"],
        "切换前后能量差 < 0.5 dB": smooth.energy_continuity_db
        < THRESHOLDS["switch_energy_continuity_db"],
        "低 RT60 末端尾音 < -100 dB": low_rt60["passed"],
        "整形后 THD < -80 dB": noise["passed"],
    }
    check_lines = "\n".join(
        f"- {'通过' if passed else '失败'}: {label}" for label, passed in checks.items()
    )
    report = f"""# FDN 混响与切换验收报告

由 `python3 run_experiment.py` 自动生成，采样率 **{SAMPLE_RATE} Hz**。

## 实现方法

- 8 路反馈延迟网络，延迟长度使用互质化的毫秒值，减少周期性梳状染色。
- 每路反馈增益为 `g_i = 10^(-3 d_i / (RT60 fs))`，有效反馈矩阵为
  `A(RT60) = diag(g_i) M`。因此改变 RT60 会逐行更新反馈矩阵。
- 房间切换时，对正交矩阵在矩阵测地线上采样，并用 smoothstep 平滑 RT60；
  状态、输出与反馈系数都不发生瞬时跳变。
- 指标使用 Schröder 反向能量积分、T20 线性回归、10 ms 短时能量、
  Welch 频谱以及 1 kHz 测试音的 THD。

## 主冲激响应

目标 RT60 **{main['target_rt60']:.2f} s**，实测 **{main['measured_rt60']:.3f} s**，
相对误差 **{100.0 * main['relative_error']:.2f}%**。

## 反馈矩阵正交性与音色

所有参与比较的矩阵使用相同延迟、输入/输出耦合及 RT60 目标。非正交矩阵由
保持特征值的对角相似变换构造，因此主要用于观察正交性而非衰减时间差异。

{_matrix_table(matrices)}

正交矩阵保持能量，衰减接近目标；非正交矩阵的条件数变大，低频倍频程电平和
倍频程起伏出现可测偏移。QR 与 Hadamard 都正交但随机实现不同，说明除了正交性，
模态耦合方式本身也会影响音色。

## 房间切换

- 80 ms 平滑切换：咔哒比 **{smooth.click_ratio:.3f}x**，相邻帧最大能量阶跃
  **{smooth.energy_step_db:.2f} dB**，前后能量差
  **{smooth.energy_continuity_db:.2f} dB**。
- 瞬时切换对照：咔哒比 **{abrupt.click_ratio:.3f}x**；平滑切换明显降低了
  不连续激励。

## 低 RT60 尾音

目标 RT60 **{low_rt60['target_rt60']:.2f} s**，生成 **{low_rt60['duration_seconds']:.2f} s**
冲激响应，不做末端淡出。最后 20 ms 尾音为
**{low_metrics['terminal_tail_db']:.1f} dB**，末样本阶跃为
**{low_metrics['final_step_db']:.1f} dB**，不会在仍有可闻能量时突然截断。

## 噪声整形

12-bit 量化，1 kHz、0.25 幅度测试音，二阶误差反馈 `(1-z^-1)^2`：

- 整形前 THD：**{noise['thd_before_db']:.2f} dB**
- 整形后 THD：**{noise['thd_after_db']:.2f} dB**
- 验收阈值：**< -80 dB**

代价是高频量化噪声被抬升；频谱图同时给出整形前后结果。

## 验收结果

{check_lines}

## 输出文件

- `artifacts/figures/main_impulse_response.png`
- `artifacts/figures/matrix_orthogonality.png`
- `artifacts/figures/room_switch.png`
- `artifacts/figures/rt60_dynamic_update.png`
- `artifacts/figures/low_rt60_tail.png`
- `artifacts/figures/noise_shaping_spectrum.png`
- `artifacts/results.json`
- `artifacts/audio/*.wav`
"""
    path.write_text(report, encoding="utf-8")


def run_all(output_root: Path | None = None) -> dict[str, object]:
    output_root = (
        Path(__file__).resolve().parent.parent / "artifacts"
        if output_root is None
        else Path(output_root)
    )
    figures = output_root / "figures"
    audio = output_root / "audio"
    output_root.mkdir(parents=True, exist_ok=True)
    figures.mkdir(parents=True, exist_ok=True)
    audio.mkdir(parents=True, exist_ok=True)

    main = run_main_experiment()
    matrices = run_matrix_experiment()
    switch = run_switch_experiment()
    dynamic_rt60 = run_rt60_transition()
    low_rt60 = run_low_rt60_experiment()
    noise = run_noise_shaping_experiment()

    plot_main_response(main, figures / "main_impulse_response.png")
    plot_matrix_comparison(matrices, figures / "matrix_orthogonality.png")
    plot_switch(switch, figures / "room_switch.png")
    plot_rt60_transition(dynamic_rt60, figures / "rt60_dynamic_update.png")
    plot_low_rt60_tail(low_rt60, figures / "low_rt60_tail.png")
    plot_noise_shaping(noise, figures / "noise_shaping_spectrum.png")

    _write_float_wav(audio / "main_impulse_response.wav", main["impulse_response"])
    _write_float_wav(audio / "room_switch.wav", switch["signal"])
    _write_float_wav(audio / "low_rt60_tail.wav", low_rt60["impulse_response"])
    _write_float_wav(audio / "rt60_dynamic_update.wav", dynamic_rt60["output"])

    results = _build_results_json(main, matrices, switch, low_rt60, noise)
    (output_root / "results.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    _write_report(
        output_root / "REPORT.md",
        main,
        matrices,
        switch,
        low_rt60,
        noise,
    )
    return results


if __name__ == "__main__":
    summary = run_all()
    print(json.dumps(summary["checks"], indent=2))
    print("all_required_checks_passed:", summary["all_required_checks_passed"])
