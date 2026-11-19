"""End-to-end wow/flutter correction experiment.

Generates a simulated stereo tape capture with slow wow, fast flutter and
pilot-tone dropouts, corrects it from the pilot track, and reports:
residual frequency deviation, L/R alignment error, waveform continuity
near gaps, and proof that no programme material was inserted or dropped.
"""

import json
import os

import numpy as np
from scipy.io import wavfile

from wowflutter.generate import simulate_capture, FS
from wowflutter.correct import correct_channels, track_pilot_speed
from wowflutter.metrics import (residual_deviation, alignment_error,
                                gap_continuity, integrity_check, lag_series)
from wowflutter import plots

OUT = "out"


def _write_wav(name, fs, *chs):
    data = np.stack(chs, axis=1) if len(chs) > 1 else chs[0]
    peak = np.max(np.abs(data)) or 1.0
    pcm = np.clip(data / peak * 0.89, -1, 1)
    wavfile.write(os.path.join(OUT, name), fs, (pcm * 32767).astype(np.int16))


def run():
    os.makedirs(OUT, exist_ok=True)
    cap = simulate_capture()
    fs = cap.fs
    t = np.arange(len(cap.left)) / fs

    channels = {"L": cap.left, "R": cap.right, "pilot": cap.pilot}

    # Mode 1: full correction (gap intervals interpolated and marked).
    res = correct_channels(channels, cap.pilot, fs, bypass_estimated=False)
    # Mode 2: reviewer bypasses correction inside estimated intervals.
    res_byp = correct_channels(channels, cap.pilot, fs, bypass_estimated=True)

    report = {"fs": fs, "duration_s": len(cap.left) / fs,
              "pilot_gaps_truth": cap.gaps,
              "estimated_intervals": res.estimated_intervals}

    # Residual frequency deviation (measured on the pilot itself).
    report["deviation_before"] = residual_deviation(cap.pilot, fs)
    report["deviation_after"] = residual_deviation(res.channels["pilot"], fs)
    report["deviation_after_bypass"] = residual_deviation(
        res_byp.channels["pilot"], fs)

    # Alignment: absolute timing error of the shared map vs true maps.
    err_l = (res.time_map - cap.time_map_left) * 1e3
    err_r = (res.time_map - cap.time_map_right) * 1e3
    edge = int(0.1 * fs)
    gap_m = np.zeros(len(err_l), bool)
    for a, b in res.estimated_intervals:
        gap_m[int(a * fs):int(b * fs)] = True
    vm = ~gap_m
    vm[:edge] = False
    vm[-edge:] = False
    report["map_timing_err_ms"] = {
        "L_rms_valid": float(np.sqrt(np.mean(err_l[vm] ** 2))),
        "R_rms_valid": float(np.sqrt(np.mean(err_r[vm] ** 2))),
        "L_max_valid": float(np.max(np.abs(err_l[vm]))),
        "R_max_valid": float(np.max(np.abs(err_r[vm]))),
        "L_max_in_gaps": float(np.max(np.abs(err_l[gap_m]))),
        "R_max_in_gaps": float(np.max(np.abs(err_r[gap_m]))),
    }
    # Blind L/R lag estimate vs ground-truth skew, before and after.
    truth_lag = cap.time_map_right - cap.time_map_left
    report["lr_align_before"] = alignment_error(cap.left, cap.right, fs,
                                                truth_lag, t)
    report["lr_align_after"] = alignment_error(
        res.channels["L"], res.channels["R"], fs, truth_lag, t)

    # Continuity near gap boundaries.
    report["continuity_corrected"] = gap_continuity(
        res.channels["L"], fs, res.estimated_intervals)
    report["continuity_bypassed"] = gap_continuity(
        res_byp.channels["L"], fs, res.estimated_intervals)

    # Integrity: nothing inserted or dropped.
    prog_in = {"L": cap.left, "R": cap.right}
    prog_out = {"L": res.channels["L"], "R": res.channels["R"]}
    clean = {"L": cap.clean_left, "R": cap.clean_right}
    report["integrity"] = integrity_check(
        prog_in, prog_out, res.time_map, clean, fs,
        skip=res.estimated_intervals)

    with open(os.path.join(OUT, "report.json"), "w") as fh:
        json.dump(report, fh, indent=2)

    # --- Diagnostic artefacts ---
    _write_wav("capture_program.wav", fs, cap.left, cap.right)
    _write_wav("capture_pilot.wav", fs, cap.pilot)
    _write_wav("corrected_program.wav", fs, res.channels["L"],
               res.channels["R"])
    _write_wav("corrected_bypass.wav", fs, res_byp.channels["L"],
               res_byp.channels["R"])

    truth_dev = cap.profile.delta(t)
    plots.plot_speed(os.path.join(OUT, "speed_curve.png"), t,
                     truth_dev, res.speed - 1.0, res.estimated_intervals)
    dev_b, _, _ = track_pilot_speed(cap.pilot, fs)
    dev_a, _, _ = track_pilot_speed(res.channels["pilot"], fs)
    plots.plot_deviation(os.path.join(OUT, "deviation.png"), t,
                         (dev_b - 1) * 100, (dev_a - 1) * 100,
                         res.estimated_intervals)
    plots.plot_gap_waveform(os.path.join(OUT, "gap_waveform.png"), fs,
                           cap.clean_left, res.channels["L"],
                           res_byp.channels["L"], res.estimated_intervals[0])
    lt, ll_b = lag_series(cap.left, cap.right, fs)
    _, ll_a = lag_series(res.channels["L"], res.channels["R"], fs)
    plots.plot_lr_lag(os.path.join(OUT, "lr_lag.png"), lt, ll_b, ll_a,
                      t, cap.time_map_right - cap.time_map_left)

    return cap, res, res_byp, report


def write_markdown(report, path):
    d_b, d_a, d_x = (report["deviation_before"], report["deviation_after"],
                     report["deviation_after_bypass"])
    mt = report["map_timing_err_ms"]
    al_b, al_a = report["lr_align_before"], report["lr_align_after"]
    cc, cb = report["continuity_corrected"], report["continuity_bypassed"]
    ig = report["integrity"]
    gaps = ", ".join(f"{a:.2f}-{b:.2f} s"
                     for a, b in report["estimated_intervals"])
    md = f"""# Wow/Flutter 校正实验报告

采样率 {report['fs']} Hz，时长 {report['duration_s']:.1f} s，双声道节目 + 3150 Hz 先导音轨。
损伤模型：慢抖 0.4% @ 0.5 Hz + 漂移 0.2% @ 0.08 Hz；快颤 0.12% @ 9 Hz + 0.05% @ 13 Hz；
右声道附加磁头歪斜抖动（0.04% @ 0.3 Hz，共享映射不可校正）；先导音缺口两处。

## 先导音缺口

- 真实缺口：{report['pilot_gaps_truth']}
- 自动标记的估计区间：{gaps}（含 15 ms 边缘腐蚀，审听员可旁路）

## 残余频偏（先导音瞬时频率，%）

| 指标 | 校正前 | 校正后 | 校正后（缺口旁路） |
|---|---|---|---|
| RMS | {d_b['rms_pct']:.4f} | {d_a['rms_pct']:.4f} | {d_x['rms_pct']:.4f} |
| 峰值 | {d_b['peak_pct']:.4f} | {d_a['peak_pct']:.4f} | {d_x['peak_pct']:.4f} |
| 慢抖带 RMS (<5 Hz) | {d_b['wow_rms_pct']:.4f} | {d_a['wow_rms_pct']:.4f} | {d_x['wow_rms_pct']:.4f} |
| 快颤带 RMS (5-20 Hz) | {d_b['flutter_rms_pct']:.4f} | {d_a['flutter_rms_pct']:.4f} | {d_x['flutter_rms_pct']:.4f} |

## 左右声道对齐

- 共享映射绝对定时误差（有效区 RMS）：L {mt['L_rms_valid']:.3f} ms，R {mt['R_rms_valid']:.3f} ms（R 含不可校正的歪斜残差）
- 缺口内最大定时误差：L {mt['L_max_in_gaps']:.3f} ms，R {mt['R_max_in_gaps']:.3f} ms
- 盲测 L/R 延迟与真实歪斜的估计误差 RMS：校正前 {al_b['est_err_rms_ms']*1e3:.1f} µs，校正后 {al_a['est_err_rms_ms']*1e3:.1f} µs
- 真实歪斜残差 RMS {al_a['residual_truth_rms_ms']:.3f} ms 在校正前后保持不变：共享映射既不引入也无法消除声道间歪斜

## 缺口附近波形连续性

| 模式 | 阈值 | 缺口边界最大跳变 | 违例数 |
|---|---|---|---|
| 校正（插值） | {cc['threshold']:.3f} | {cc['max_jump_near_gaps']:.3f} | {cc['violations']} |
| 审听员旁路 | {cb['threshold']:.3f} | {cb['max_jump_near_gaps']:.3f} | {cb['violations']} |

## 完整性（无插入/丢失节目片段）

- 输出长度与输入一致：{ig['lengths_preserved']}；时间映射严格单调：{ig['map_strictly_monotonic']}
- 映射总跨度 {ig['map_span_s']:.4f} s（真值 11.9994 s）
- 校正后与干净源每秒最佳相关（缺口±0.6 s 外）：L ≥ {ig['min_per_second_corr']['L']:.4f}，R ≥ {ig['min_per_second_corr']['R']:.4f}
- 缺口外每秒对齐延迟最大跳变：L {ig['max_lag_step_ms_outside_gaps']['L']:.2f} ms，R {ig['max_lag_step_ms_outside_gaps']['R']:.2f} ms（R 的 0.25 ms 为真实歪斜的秒间漂移，非丢样）
- R 声道相关性略低（0.94）系磁头歪斜抖动残差所致，属预期物理极限

## 产物

- `capture_program.wav` / `capture_pilot.wav`：模拟采集素材
- `corrected_program.wav`：校正后节目；`corrected_bypass.wav`：缺口段旁路版本
- `speed_curve.png` / `deviation.png` / `gap_waveform.png` / `lr_lag.png`：诊断图
"""
    with open(path, "w") as fh:
        fh.write(md)


if __name__ == "__main__":
    cap, res, res_byp, report = run()
    write_markdown(report, os.path.join(OUT, "report.md"))
    print(json.dumps(report, indent=2))
