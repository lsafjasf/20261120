"""Render reports/verification_report.json into a human-readable summary."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    r = json.loads((ROOT / "reports" / "verification_report.json").read_text())
    v = r["video_smartcut"]
    a = r["audio_smartcut"]
    s = r["subtitles_smartcut"]
    b = r["byte_comparison"]
    k = r["streamcopy_keyframe_alignment"]
    d = r["deblocking"]["verdict_at_production_crf20"]
    plan = r["reencode_plan"]

    lines = [
        "# 精确剪辑导出验证报告",
        "",
        f"剪辑区间: 源帧 {r['cut_range_frames'][0]} -> "
        f"{r['cut_range_frames'][1]}（不含），共 "
        f"{r['cut_range_frames'][1] - r['cut_range_frames'][0]} 帧",
        "",
        "## 1. 局部重编码 + 流复制分段",
        "",
        "| 段 | 方式 | 源 GOP 帧 | 保留帧 | 重编码帧数 |",
        "|----|------|-----------|--------|-----------|",
    ]
    for seg in plan:
        lines.append(
            f"| {seg['file'].split('/')[-1]} | {seg['kind']} | "
            f"{seg['src_gop_frames'][0]}..{seg['src_gop_frames'][1]} | "
            f"{seg['kept_frames'][0]}..{seg['kept_frames'][1]} | "
            f"{seg['reencoded_frames']} |")
    lines += [
        "",
        f"重编码帧总数: **{r['reencoded_frame_total']}** "
        f"（其余 {v['expected_frames'] - r['reencoded_frame_total']} 帧包级流复制）",
        "",
        "## 2. 输出哈希与帧数",
        "",
        "| 文件 | 字节数 | SHA-256 |",
        "|------|--------|---------|",
    ]
    for name, meta in r["file_hashes"].items():
        lines.append(f"| {name} | {meta['size_bytes']} | `{meta['sha256']}` |")
    lines += [
        "",
        f"智能剪辑解码帧数: {v['decoded_frames']} / 期望 {v['expected_frames']}"
        f"（匹配: {v['frame_count_match']}）",
        f"帧身份序列与源逐帧一致: **{v['frame_identity_sequence_ok']}**",
        f"平均/最低 PSNR: {v['psnr_mean_db']:.2f} / {v['psnr_min_db']:.2f} dB",
        "",
        "## 3. 音轨未被重编码",
        "",
        f"- 编码器参数不变: **{a['codec_unchanged']}**"
        f"（{a['codec_name']} {a['sample_rate']} Hz {a['channels']} 声道）",
        f"- ADTS 帧 {a['adts_frames_clip']} 个，"
        f"与源字节一致: **{a['adts_frames_bit_identical_to_source']} 个**"
        f"（all_copied={a['all_audio_frames_copied']}）",
        f"- 音视频起点偏移: {a['av_start_offset_ms']} ms",
        "",
        "## 4. 字幕同步",
        "",
        f"- 字幕条数: {s['cue_count']}，首条起点归零: {s['starts_at_zero']}",
        f"- 全部 cue 落在片段内: {s['all_cues_within_clip']}，"
        f"最大时间戳量化误差: {s['max_timestamp_quantization_error_ms']} ms",
        f"- 视频时长 {s['video_duration_s']:.3f}s，末条结束 {s['last_cue_end_s']}s",
        "",
        "## 5. 流复制 vs 局部重编码字节差异",
        "",
        f"- 流复制输出 {b['streamcopy']['size_bytes']} 字节，"
        f"智能剪辑 {b['smartcut']['size_bytes']} 字节，"
        f"容器差 {b['container_size_delta_bytes']} 字节",
        f"- H.264 基本流: 复制 {b['h264_es_size_copy']} B / "
        f"智能 {b['h264_es_size_smart']} B；MP4 与 ES 均非字节相同"
        f"（identical={b['identical_mp4_bytes']}）",
        f"- NAL 单元数: 复制 {b['streamcopy']['h264_nal_units']} / "
        f"智能 {b['smartcut']['h264_nal_units']}",
        f"- 纯流复制起点落在前一关键帧 {k['actual_first_frame']}"
        f"（请求 {k['requested_start_frame']}，多 {k['extra_head_frames']} 帧，"
        f"帧精确={k['frame_accurate']}）",
        "",
        "## 6. 编辑点无缝衔接",
        "",
    ]
    for seam, m in v["seams"].items():
        lines.append(
            f"- {seam}: 运动场最大误差 {m['motion_field_max_err']}，"
            f"平均 {m['motion_field_mean_err']:.4f}，"
            f"无缝判定 **{m['seamless']}**")
    lines += [
        "",
        "## 7. 去块滤波不模糊真实边缘（生产 CRF20）",
        "",
        f"- 真实边缘像素差（去块开/关）: "
        f"{d['true_edge_mean_abs_diff_deblock_on']:.4f} / "
        f"{d['true_edge_mean_abs_diff_deblock_off']:.4f}",
        f"- 真实边缘像素级变化量: {d['true_edge_pixel_level_delta']:.5f}"
        f"（< 0.05，无可见模糊: **{d['real_edges_not_blurred']}**）",
        f"- 块边界平滑增益: {d['block_boundary_smoothing_gain']:.5f}"
        f"（>=0 表示抑制块效应: **{d['blocking_reduced']}**）",
        "",
        "完整机器可读数据见 `reports/verification_report.json`。",
        "",
    ]
    (ROOT / "reports" / "summary.md").write_text("\n".join(lines),
                                                  encoding="utf-8")
    print("wrote reports/summary.md")


if __name__ == "__main__":
    main()
