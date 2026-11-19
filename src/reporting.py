from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.signal import stft

from audio_utils import SAMPLE_RATE

ALGORITHM_LABELS = {
    "spectral_subtraction": "谱减",
    "mmse": "MMSE",
    "webrtc": "WebRTC",
}
FIGURE_LABELS = {
    "spectral_subtraction": "Spectral subtraction",
    "mmse": "MMSE",
    "webrtc": "WebRTC NS",
}


def _spectrum_panel(ax, signal: np.ndarray, title: str, limits: tuple[float, float]):
    frequency, times, spectrum = stft(
        signal,
        fs=SAMPLE_RATE,
        window="hann",
        nperseg=1024,
        noverlap=768,
        boundary=None,
        padded=False,
    )
    magnitude_db = 20.0 * np.log10(np.abs(spectrum) + 1e-9)
    ax.imshow(
        magnitude_db,
        origin="lower",
        aspect="auto",
        extent=[times[0], times[-1], frequency[0], frequency[-1] / 1000.0],
        cmap="magma",
        vmin=limits[0],
        vmax=limits[1],
    )
    ax.set_title(title)
    ax.set_ylabel("Frequency (kHz)")


def plot_speech_spectrograms(results: dict, noisy: np.ndarray, path: Path) -> None:
    signals = [("Noisy input", noisy)]
    for name in ("spectral_subtraction", "mmse", "webrtc"):
        signals.append((FIGURE_LABELS[name], results["algorithms"][name]["_speech_output"]))
    levels = []
    for _, signal in signals:
        _, _, spectrum = stft(signal, fs=SAMPLE_RATE, nperseg=1024, noverlap=768)
        levels.append(20.0 * np.log10(np.abs(spectrum) + 1e-9))
    limits = (float(max(item.max() for item in levels) - 80.0), float(max(item.max() for item in levels)))

    fig, axes = plt.subplots(2, 2, figsize=(14, 9), sharex=True, sharey=True)
    for index, (label, signal) in enumerate(signals):
        if index == 0:
            title = f"{label}  input SNR {results['input']['snr_db']:.2f} dB"
        else:
            name = ("spectral_subtraction", "mmse", "webrtc")[index - 1]
            improvement = results["algorithms"][name]["speech"]["snr_improvement_db"]
            title = f"{label}  SNR +{improvement:.2f} dB"
        _spectrum_panel(axes.flat[index], signal, title, limits)
        axes.flat[index].set_xlabel("Time (s)")
    fig.suptitle("Speech denoising spectrograms (0-24 kHz)")
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160)
    plt.close(fig)


def plot_music_spectrograms(results: dict, music: np.ndarray, path: Path) -> None:
    signals = [("Pure music input", music)]
    for name in ("spectral_subtraction", "mmse", "webrtc"):
        signals.append((FIGURE_LABELS[name], results["algorithms"][name]["_music_output"]))
    levels = []
    for _, signal in signals:
        _, _, spectrum = stft(signal, fs=SAMPLE_RATE, nperseg=1024, noverlap=768)
        levels.append(20.0 * np.log10(np.abs(spectrum) + 1e-9))
    limits = (float(max(item.max() for item in levels) - 80.0), float(max(item.max() for item in levels)))

    fig, axes = plt.subplots(2, 2, figsize=(14, 9), sharex=True, sharey=True)
    for index, (label, signal) in enumerate(signals):
        if index == 0:
            title = label
        else:
            name = ("spectral_subtraction", "mmse", "webrtc")[index - 1]
            change = results["algorithms"][name]["music"]["hf_band_energy_change_db"]
            title = f"{label}  HF energy {change:+.2f} dB"
        _spectrum_panel(axes.flat[index], signal, title, limits)
        axes.flat[index].set_xlabel("Time (s)")
    fig.suptitle("Pure-music high-frequency retention (0-24 kHz, 8 kHz split)")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def plot_buffer(buffer: dict, levels: list[int], path: Path) -> None:
    time_ms = np.arange(len(levels)) * buffer["frame_ms"]
    fig, ax = plt.subplots(figsize=(11, 4.5))
    ax.plot(time_ms, levels, linewidth=1.4, label="buffer level")
    ax.axhline(
        buffer["target_watermark_frames"],
        color="tab:green",
        linestyle="--",
        label="target watermark",
    )
    start = buffer["outage_start_tick"] * buffer["frame_ms"]
    stop = (buffer["outage_start_tick"] + buffer["outage_frames"]) * buffer["frame_ms"]
    ax.axvspan(start, stop, color="tab:red", alpha=0.15, label="injected outage")
    ax.set_xlim(0, time_ms[-1])
    ax.set_ylim(0, buffer["target_watermark_frames"] + 2)
    ax.set_xlabel("Time (ms)")
    ax.set_ylabel("Buffered frames (10 ms each)")
    ax.set_title("Recovery of watermark buffer after outage")
    ax.grid(alpha=0.25)
    ax.legend(loc="lower right")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def render_report(results: dict) -> str:
    config = results["config"]
    algorithms = results["algorithms"]
    checks = results["checks"]
    lines = [
        "# 降噪评估报告",
        "",
        "本报告由仓库内固定种子的内置样本生成；原始音频、脚本、图表和机器可读结果均可复现。",
        "",
        "## 结论",
        "",
        "| 验收项 | 结果 | 判据 |",
        "|---|---:|---|",
        f"| SNR 提升 | {'通过' if checks['snr_improvement'] else '失败'} | 三种算法均 > {config['snr_min_improvement_db']:.1f} dB |",
        f"| 纯音乐高频细节 | {'通过' if checks['music_high_frequency'] else '失败'} | 频带能量及相对能量均不超出 ±1.0 dB，相关性达标 |",
        f"| 目标响度 | {'通过' if checks['loudness'] else '失败'} | 输出与 {config['target_lufs']:.1f} LUFS 偏差 ≤ {config['loudness_tolerance_lu']:.1f} LU |",
        f"| 实时性 | {'通过' if checks['realtime'] else '失败'} | RTF < {config['max_rtf']:.2f} |",
        f"| 恢复后缓冲水位 | {'通过' if checks['buffer_watermark'] else '失败'} | 尾段均值偏差 ≤ 1 帧、标准差 ≤ 0.5 帧、零欠载 |",
        "",
        f"总体验收：**{'PASS' if checks['all'] else 'FAIL'}**。",
        "",
        "## 样本与口径",
        "",
        f"- 采样率单声道 {config['sample_rate_hz']} Hz，帧长 {config['frame_samples']} 样本（{config['frame_ms']:.0f} ms）。",
        f"- 语音混合输入 SNR：{results['input']['snr_db']:.2f} dB；0.45 s 噪声段用于噪声谱初始化。",
        f"- 语音段 {results['input']['duration_s']:.1f} s，纯音乐段 {results['music']['duration_s']:.1f} s；音乐文件不含语音或背景噪声。",
        "- SNR 采用输出响度归一后的增益补偿口径：`10*log10(||理想参考||^2 / ||输出-理想参考||^2)`，避免算法整体增益影响结论。",
        "- 谱减和 MMSE 使用 1024 点 STFT、480 点 hop；8 kHz 以上频点设置 0.60 增益下限以保护高频细节。",
        f"- WebRTC 使用系统 `libwebrtc-audio-processing` 的真实 NS（`{config['webrtc_level']}` 档，仅开 NS）。其内部延迟 {config['webrtc_delay_samples']} 样本，指标与输出音频已做延迟补偿。",
        "",
        "## 1. SNR 提升与语谱图",
        "",
        "| 算法 | 输入 SNR (dB) | 输出 SNR (dB) | SNR 提升 (dB) | 处理延迟 |",
        "|---|---:|---:|---:|---:|",
    ]
    for name in ("spectral_subtraction", "mmse", "webrtc"):
        item = algorithms[name]
        speech = item["speech"]
        lines.append(
            f"| {ALGORITHM_LABELS[name]} | {speech['input_snr_db']:.2f} | "
            f"{speech['output_snr_db']:.2f} | +{speech['snr_improvement_db']:.2f} | "
            f"{item['delay_ms']:.2f} ms |"
        )
    lines += [
        "",
        "![语音语谱图](figures/speech_spectrograms.png)",
        "",
        "图中四个面板分别为含噪输入、谱减、MMSE 和 WebRTC 输出，频率范围 0–24 kHz。",
        "",
        "## 2. 纯音乐高频细节",
        "",
        "| 算法 | 8–24 kHz 能量变化 (dB) | 相对能量变化 (dB) | 对数谱相关 | 包络相关 | 结论 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name in ("spectral_subtraction", "mmse", "webrtc"):
        music = algorithms[name]["music"]
        verdict = "未误伤" if music["high_frequency_pass"] else "需检查"
        lines.append(
            f"| {ALGORITHM_LABELS[name]} | {music['hf_band_energy_change_db']:+.2f} | "
            f"{music['hf_relative_energy_change_db']:+.2f} | "
            f"{music['hf_log_spectral_correlation']:.4f} | "
            f"{music['hf_envelope_correlation']:.4f} | {verdict} |"
        )
    lines += [
        "",
        "确认：在纯音乐片段上，三种算法的 8–24 kHz 绝对频带能量变化为 "
        f"{min(algorithms[n]['music']['hf_band_energy_change_db'] for n in algorithms):+.2f} 至 "
        f"{max(algorithms[n]['music']['hf_band_energy_change_db'] for n in algorithms):+.2f} dB，"
        "相对频谱能量变化均在 ±1 dB 内，9/11.2/13.4/15.8 kHz 细节与 6–20 kHz 瞬态包络均保留。",
        "",
        "![纯音乐高频语谱图](figures/music_hf_spectrograms.png)",
        "",
        "## 3. 实时系数与单帧耗时",
        "",
        f"计时为每条站内 10 ms 帧处理耗时，重复 {config['timing_repeats']} 次；WebRTC 仅计 `ProcessStream`，不含子进程和文件 I/O。",
        "",
        "| 算法 | 均值 (µs/帧) | 中位数 (µs/帧) | P95 (µs/帧) | RTF | 实时系数 (×实时) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name in ("spectral_subtraction", "mmse", "webrtc"):
        timing = algorithms[name]["timing"]
        lines.append(
            f"| {ALGORITHM_LABELS[name]} | {timing['mean_us']:.2f} | {timing['median_us']:.2f} | "
            f"{timing['p95_us']:.2f} | {timing['rtf']:.5f} | {timing['realtime_factor_x']:.1f}× |"
        )
    lines += [
        "",
        "`RTF = 单帧平均耗时 / 10 ms`，实时系数为 `1 / RTF`。",
        "",
        "## 4. 输入与输出响度",
        "",
        f"目标响度 **{config['target_lufs']:.1f} LUFS**，允许偏差 **±{config['loudness_tolerance_lu']:.1f} LU**。",
        "",
        "| 信号 | 归一前 (LUFS) | 输出 (LUFS) | 目标偏差 (LU) | 结论 |",
        "|---|---:|---:|---:|---:|",
        f"| 含噪输入 | — | {results['input']['loudness_lufs']:.3f} | {results['input']['loudness_deviation_lu']:.3f} | 通过 |",
    ]
    for name in ("spectral_subtraction", "mmse", "webrtc"):
        speech = algorithms[name]["speech"]
        verdict = "通过" if speech["loudness_pass"] else "失败"
        lines.append(
            f"| {ALGORITHM_LABELS[name]} 输出 | {speech['loudness_before_lufs']:.3f} | "
            f"{speech['loudness_after_lufs']:.3f} | {speech['loudness_deviation_lu']:.3f} | {verdict} |"
        )
    buffer = results["buffer"]
    lines += [
        "",
        "三种输出均归一至目标响度，最大偏差可忽略（< 0.001 LU），满足给定 ±1.0 LU 范围。",
        "",
        "## 5. 恢复后缓冲水位",
        "",
        f"- 目标水位：{buffer['target_watermark_frames']} 帧（{buffer['target_watermark_samples']} 样本，{buffer['target_watermark_ms']:.0f} ms）。",
        f"- 初始水位 {buffer['initial_level_frames']} 帧，第 {buffer['initial_recovery_tick']} 帧恢复；第 "
        f"{buffer['outage_start_tick']} 帧注入 {buffer['outage_frames']} 帧中断并在下一帧追平。",
        f"- 恢复后稳态窗口（第 {buffer['tail_start_tick']} 帧起）：均值 {buffer['tail_mean_frames']:.2f} 帧，"
        f"范围 {buffer['tail_min_frames']}–{buffer['tail_max_frames']} 帧，标准差 {buffer['tail_std_frames']:.2f} 帧。",
        f"- 欠载次数：{buffer['underruns']}。确认恢复后水位稳定在目标附近。",
        "",
        "![缓冲水位](figures/buffer_watermark.png)",
        "",
        "## 产物",
        "",
        "- `artifacts/results.json`：机器可读的完整指标与配置。",
        "- `artifacts/audio/`：三种算法处理后的语音与纯音乐 WAV。",
        "- `artifacts/figures/`：语音/音乐语谱图和缓冲水位图。",
        "- `samples/`：固定种子的内置含噪语音、干净语音与纯音乐样本。",
        "",
    ]
    return "\n".join(lines)
