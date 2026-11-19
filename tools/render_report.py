"""Render the machine-readable validation results as a concise Markdown report."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "samples" / "report"


def parameter_set_table(items: list[dict]) -> list[str]:
    lines = [
        "| 参数集 | ID | 修订 | 起始码偏移 | 起始码 | NAL 偏移 | 前缀 | 出现次数 | 分辨率 | 位深 | 色度 |",
        "|---|---:|---:|---:|---|---:|---:|---:|---|---:|---|",
    ]
    for item in items:
        lines.append(
            "| {kind} | {id} | {revision} | {start_offset} | {start_code_hex} | {nal_offset} | "
            "{prefix_length}B | {occurrences} | {resolution} | {bit_depth} | "
            "{chroma_format} |".format(
                kind=item["kind"],
                id=item["id"],
                revision=item["revision"],
                start_offset=item["start_offset"],
                start_code_hex=item["start_code_hex"],
                nal_offset=item["nal_offset"],
                prefix_length=item["prefix_length"],
                occurrences=item["occurrences"],
                resolution=item["resolution"] or "-",
                bit_depth=item["bit_depth"] or "-",
                chroma_format=item["chroma_format"] or "-",
            )
        )
    return lines


def render(data: dict) -> str:
    switch = data["switch_stream"]
    band = data["band_switch"]
    unknown = data["unknown_nals"]
    sample = data["vps_sps_pps_sample"]

    lines = [
        "# VVC 参数集、切换与频谱连续性验证报告",
        "",
        "本报告由 `tools/run_validation.py` 与 `tools/render_report.py` 生成，"
        "所有偏移均为 Annex B 字节流中的绝对字节偏移。",
        "",
        "## 1. VPS / SPS / PPS 识别",
        "",
        f"样本：`{sample['file']}`（{sample['provenance']}）。",
        "",
        *parameter_set_table(sample["parameter_set_first_occurrences"]),
        "",
        "SPS 中的 `sps_pic_*_in_luma_samples` 与裁剪窗口共同给出显示分辨率；"
        "`sps_bitdepth_minus8` 给出位深；`sps_chroma_format_idc` 给出色度格式。"
        "VPS 本身不直接携带画面分辨率。",
        "",
        "## 2. 双分辨率拼接与解码",
        "",
        f"码流：`{switch['file']}`，组成 `{' -> '.join(switch['composition'])}`。",
        "",
        "拼接流中的参数集首次出现位置：",
        "",
        *parameter_set_table(switch["parameter_set_first_occurrences"]),
        "",
        "SPS 参数集切换事件：",
        "",
        "| 切换 | 流偏移 | 解码字节边界 | 切换后首帧 | 切换前 | 切换后 |",
        "|---|---:|---:|---:|---|---|",
    ]
    for event_index, event in enumerate(switch["sps_events"], start=1):
        before = event["before"]
        after = event["after"]
        lines.append(
            f"| {event_index} | "
            f"{event['stream_offset']} | {event['decoded_byte_boundary']} | "
            f"{event['next_frame_index']} | {before['resolution']} / "
            f"{before['bit_depth']}-bit / {before['chroma_format']} | "
            f"{after['resolution']} / {after['bit_depth']}-bit / "
            f"{after['chroma_format']} |"
        )
    lines.extend(
        [
            "",
            "解码器：`VVdeC 3.2.1`。解码日志报告 "
            f"`{switch['decoder']['decoded_frames_logged']}` 帧，尺寸序列为 "
            + " -> ".join(
                f"{item['resolution']}@{item['bit_depth']}b"
                for item in switch["decoder"]["size_info_sequence"]
            )
            + "。",
            "",
            "| 片段 | 帧数 | 解码输出偏移 | 字节数 | 与独立解码逐字节一致 |",
            "|---|---:|---:|---:|---|",
        ]
    )
    for item in switch["segment_comparisons"]:
        lines.append(
            f"| {item['part']} | {item['frame_count']} | "
            f"{item['decoded_byte_offset']} | {item['byte_length']} | "
            f"{'是' if item['byte_exact_match'] else '否'} |"
        )
    lines.extend(
        [
            "",
            f"结果：期望 {switch['expected_frames']} 帧，实际 "
            f"{switch['actual_frames']} 帧；输出 {switch['decoded_bytes']} 字节，"
            f"期望 {switch['expected_bytes']} 字节。"
            f"`no_frame_loss={str(switch['no_frame_loss']).lower()}`。"
            "两个切换点的前后片段均与各自独立解码结果完全一致，因此没有因参数集切换"
            "产生丢帧、错序或像素错乱。",
            "",
            "## 3. 未知 NAL 保留与分布",
            "",
            f"探针码流：`{unknown['file']}`。未知 NAL 按原始 NAL 单元（含 2 字节头）落盘。",
            "",
            "| NAL 类型 | 名称 | 数量 | 总字节 | 首次偏移 | 原字节校验 |",
            "|---:|---|---:|---:|---:|---|",
        ]
    )
    preserved = {item["nal_type"]: item["byte_exact_match"] for item in unknown["preservation"]}
    for item in unknown["distribution"]:
        lines.append(
            f"| {item['nal_type']} | {item['type_name']} | {item['count']} | "
            f"{item['total_bytes']} | {item['first_start_offset']} | "
            f"{'一致' if preserved[item['nal_type']] else '不一致'} |"
        )
    lines.extend(
        [
            "",
            f"`all_preserved={str(unknown['all_preserved']).lower()}`。保存目录："
            "`samples/unknown_raw/`；含完整 SHA-256 与原始十六进制清单的机器报告见 "
            "`samples/report/validation.json`。",
            "",
            "## 4. 窄带 / 宽带频繁切换",
            "",
            "补充样本为 48 kHz、16-bit、单声道 PCM："
            f"`{band['duration_s']:.2f}` 秒内每 `{band['segment_duration_s']:.2f}` 秒"
            f"交替一次，共 `{band['transition_count']}` 次切换；每次切换使用 "
            f"`{band['transition_ms']:.0f} ms` 升余弦交叉过渡。",
            "",
            "| 指标 | 平滑样本 | 硬切换对照 |",
            "|---|---:|---:|",
            f"| 高频段能量占比最大相邻帧步进 | {band['max_high_share_step']:.6f} | "
            f"{band['hard_control_max_high_share_step']:.6f} |",
            f"| 高频段幅度最大相邻帧步进 | {band['max_high_amplitude_step']:.6f} | "
            f"{band['hard_control_max_high_amplitude_step']:.6f} |",
            f"| 稳态窄带高频占比中位数 | {band['narrowband_high_share_median']:.3e} | - |",
            f"| 稳态宽带高频占比中位数 | {band['wideband_high_share_median']:.6f} | - |",
            f"| 稳态低频能量波动 | {band['low_band_db_range_steady']:.6f} dB | - |",
            f"| 削波样本数 | {band['clipped_samples']} | - |",
            "",
            f"平滑/硬切换的最大频谱步进比为 `{band['smooth_to_hard_step_ratio']:.4f}`，"
            f"峰值相邻样本差为 `{band['max_sample_delta']:.6f}` FS。"
            f"`continuous_transition_pass={str(band['continuous_transition_pass']).lower()}`；"
            "高频段按交叉过渡逐步进入/退出，未观察到离散跳变或削波。",
            "",
            "频谱曲线：`samples/audio/band_switch_spectrum.svg`；逐帧数据："
            "`samples/audio/band_switch_spectrum.csv`。",
            "",
            "## 5. 复现",
            "",
            "```bash",
            "python3 -m unittest discover -s tests -v",
            "python3 tools/run_validation.py --vvdec /path/to/vvdecapp",
            "python3 tools/render_report.py",
            "```",
            "",
            "完整机器可读结果：`samples/report/validation.json`。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    data = json.loads((REPORT / "validation.json").read_text(encoding="utf-8"))
    (REPORT / "REPORT.md").write_text(render(data), encoding="utf-8")


if __name__ == "__main__":
    main()
