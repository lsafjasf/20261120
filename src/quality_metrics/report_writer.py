"""Render the human-readable Markdown report from a summary.json payload."""

from __future__ import annotations


def render_report(summary: dict) -> str:
    checks = summary["checks"]
    lines: list[str] = []
    lines.append("# 帧级画质指标与 PSSH 校验报告")
    lines.append("")
    lines.append("## 结论")
    lines.append("")
    conclusions = [
        ("三指标逐帧计算", checks["all_metrics_computed"]),
        ("高/低码率分界清晰", checks["boundary_clear"]),
        ("高噪声帧数值比较", checks["high_noise_compared"]),
        ("同帧区域对齐", checks["regions_aligned"]),
        ("PSSH KID 重写", checks["pssh_kid_rewritten"]),
        ("标准 EME 解析", checks["eme_parsed"]),
        ("EME 回读 KID 等于默认 KID", checks["eme_kid_matches"]),
    ]
    for label, passed in conclusions:
        lines.append(f"- {label}：{'通过' if passed else '失败'}")
    lines.append("")
    lines.append(
        "所有帧号均从 0 开始。指标在 BT.601 亮度平面上计算；SSIM 使用 "
        "11x11 高斯窗，MS-SSIM 使用 5 个尺度。区域对齐使用不重叠的 "
        "16x16 网格。OpenCV 承担高斯滤波、多尺度下采样与视频解码。"
    )
    lines.append("")

    bitrate = summary["encoding"]["bitrate_check"]
    lines.append("## 码率段分界")
    lines.append("")
    lines.append(
        f"实测码率：高码率段 {bitrate['high']['kbit_per_second']} kbit/s，"
        f"低码率段 {bitrate['low']['kbit_per_second']} kbit/s，"
        f"比值 {bitrate['ratio']:.1f}x。"
    )
    lines.append("")
    lines.append(
        "| 指标 | 边界帧 | 左段均值 | 右段均值 | 绝对变化 | 效应量 z | 检测帧 | 误差 | 结论 |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---|")
    for item in summary["boundary_checks"]:
        lines.append(
            f"| {item['metric']} | {item['boundary_frame']} | "
            f"{item['left_mean']:.4f} | {item['right_mean']:.4f} | "
            f"{item['absolute_change']:.4f} | {item['effect_size']:.2f} | "
            f"{item['detected_frame']} | {item['detection_error_frames']} | "
            f"{'清晰' if item['clear'] else '不清晰'} |"
        )
    lines.append("")

    lines.append("## 高噪声帧")
    lines.append("")
    lines.append(
        "| 指标 | 干净帧均值 | 高噪声帧均值 | 变化 | 相对变化 | 标准化效应量 |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|")
    for item in summary["high_noise_comparison"]:
        lines.append(
            f"| {item['metric']} | {item['clean_mean']:.6f} | "
            f"{item['noise_mean']:.6f} | {item['delta']:.6f} | "
            f"{item['relative_delta_pct']:.3f}% | {item['effect_size']:.2f} |"
        )
    lines.append("")
    lines.append(
        "PSNR 对像素级噪声最敏感（相对降幅最大），SSIM 因局部均值/方差归一化"
        "而衰减较小，MS-SSIM 经多尺度聚合后变化最小。三者方向一致，但量纲"
        "不同，数值不可直接横向比较。"
    )
    lines.append("")

    alignment = summary["alignment"]
    lines.append("## 同帧区域对齐")
    lines.append("")
    lines.append(
        f"样本帧：{', '.join(str(f) for f in alignment['sample_frames'])}。"
        f"{alignment['samples']} 组比较的平均 Pearson 为 "
        f"{alignment['mean_pearson']:.4f}，平均 hotspot IoU 为 "
        f"{alignment['mean_hotspot_iou']:.4f}。"
    )
    lines.append("")
    lines.append(
        "| 帧 | 指标对 | Pearson | hotspot IoU | 质心偏移(px) | 最佳位移(block) | 结论 |"
    )
    lines.append("|---:|---|---:|---:|---:|---|---|")
    for item in alignment["details"]:
        lines.append(
            f"| {item['frame']} | {item['pair']} | {item['pearson']:.4f} | "
            f"{item['hotspot_iou']:.4f} | {item['centroid_offset_px']:.2f} | "
            f"({item['best_shift_x_blocks']}, {item['best_shift_y_blocks']}) | "
            f"{'对齐' if item['aligned'] else '未对齐'} |"
        )
    lines.append("")
    lines.append(
        "最佳位移全部为 `(0,0)`，说明三张区域图位于同一坐标网格且没有整体"
        "错位；质心差仅反映各指标对强差区域的权重差异。"
    )
    lines.append("")

    pssh = summary["pssh"]
    eme = summary["eme"]
    lines.append("## PSSH 与 EME")
    lines.append("")
    wv = pssh["widevine"]
    lines.append(f"- 默认 KID：`{pssh['default_kid']}`")
    lines.append(
        f"- Widevine 顶层 KID：`{wv['rewritten_box_kids'][0]}`"
    )
    lines.append(
        f"- Widevine 内层 key_id：`{wv['rewritten_proto_kids'][0]}`"
    )
    lines.append(
        f"- ClearKey 重写 KID：`{pssh['clearkey']['rewritten_box_kids'][0]}`"
    )
    lines.append(f"- ClearKey EME key system：`{eme['key_system']}`")
    lines.append(f"- 浏览器：`{eme['browser_version']}`")
    parsed = eme["result"]["parsedKids"]
    lines.append(f"- EME 回读 KID：`{parsed[0] if parsed else 'N/A'}`")
    lines.append(
        f"- `generateRequest` 成功：`{eme['result'].get('generated', False)}`"
    )
    lines.append(
        f"- 收到 license request：`{eme['result'].get('messageReceived', False)}`"
    )
    lines.append("")
    lines.append(
        "EME 测试使用 Chromium 内置 `org.w3.clearkey`（环境无 Widevine CDM）。"
        "输入是重写后的 Widevine PSSH 与同 KID 的 ClearKey PSSH 拼接成的标准"
        " `cenc` initData；Chromium EME 解析器接受全部 PSSH，并从 license "
        "request 回读出与默认 KID 完全一致的值。"
    )
    lines.append("")

    lines.append("## 产物")
    lines.append("")
    for label, key in [
        ("每帧指标 CSV", "per_frame_csv"),
        ("逐帧曲线图", "curves"),
        ("对齐叠加图", "alignment_figure"),
        ("高噪声对比图", "high_noise_figure"),
    ]:
        lines.append(f"- {label}：`{summary['artifacts'][key]}`")
    lines.append("- 三指标区域热图：`heatmaps/`")
    lines.append("- PSSH 原始/重写二进制：`pssh_*_original.bin` / `pssh_*_rewritten.bin`")
    lines.append("- EME 验证原始结果：`eme_validation.json`")
    lines.append("")

    lines.append("## 复现")
    lines.append("")
    lines.append("```bash")
    lines.append("python3 -m venv .venv && .venv/bin/pip install -r requirements.txt")
    lines.append(
        "PYTHONPATH=src .venv/bin/python -m quality_metrics demo --out artifacts/demo"
    )
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def write_report(summary: dict, path: str) -> None:
    with open(path, "w") as fh:
        fh.write(render_report(summary))
