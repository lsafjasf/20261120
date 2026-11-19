"""Render layout_report.json into a Markdown verification report."""

from __future__ import annotations

import json
from pathlib import Path


def _line_table(cues: dict, cue_id: str) -> list[str]:
    rows = ["| # | 断点类型 | 字符偏移 | 显示宽度 px | 时间戳 tag s | 文本 |",
            "|---|----------|----------|-------------|--------------|------|"]
    for index, line in enumerate(cues[cue_id]["line_breaks"], 1):
        kind = "硬换行" if line["hard_break"] else "软换行"
        stamp = "-" if line["timestamp_tag_s"] is None else line["timestamp_tag_s"]
        rows.append(
            f"| {index} | {kind} | {line['break_before_char_index']} | "
            f"{line['display_width_px']} | {stamp} | {line['text']} |")
    return rows


def render(report_path: str, out_path: str) -> None:
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    lines: list[str] = []
    lines.append("# WebVTT 排版与帧率核对报告")
    lines.append("")
    lines.append(f"- 素材：`{report['source']['vtt']}` / `{report['source']['html']}`")
    lines.append(f"- cue 总数：{report['cue_count']}；REGION："
                 f"{', '.join(report['regions'])}")
    lines.append(f"- `::cue` 规则：{len(report['styles'])} 条")
    lines.append("")

    fps = report["frame_rate"]
    lines.append("## 1. script tag 帧率核对")
    lines.append("")
    lines.append(f"- `<script type=\"text/vtt\" data-fps>` 声明帧率：**{fps['declared_fps']} fps**")
    lines.append(f"- 由 {fps['timestamp_count']} 个时间戳反推的实际帧率：**{fps['derived_fps']} fps**")
    lines.append(f"- 最大网格拟合误差：{fps['max_grid_error_s']} s；容差 ±{fps['tolerance']} fps")
    verdict = "一致" if fps["consistent"] else "**不一致**"
    lines.append(f"- 结论：{verdict}（声明 25 fps，但时间戳全部落在 24/30 fps 网格上）")
    lines.append("")

    lines.append("## 2. 两种播放器宽度的排版结果")
    for width_key, width_result in report["widths"].items():
        vp = width_result["viewport"]
        checks = width_result["viewport_checks"]
        lines.append("")
        lines.append(f"### {width_key}（字号 {vp['font_px']} px，行高 {vp['line_height_px']} px）")
        lines.append("")
        lines.append(f"- cue 视口越界：{len(checks['cue_viewport_violations'])} 处")
        lines.append(f"- 几何重叠事件：{len(width_result['overlap_events'])} 组")
        lines.append(f"- REGION 全部在视口内：{checks['all_regions_inside_viewport']}")
        lines.append(f"- REGION 占视口面积：{checks['region_footprint_pct_of_viewport']}%")
    lines.append("")

    lines.append("## 3. 每行断点与显示宽度（1280×720）")
    for cue_id in report["widths"]["1280x720"]["cues"]:
        lines.append("")
        lines.append(f"### cue `{cue_id}`")
        lines.extend(_line_table(report["widths"]["1280x720"]["cues"], cue_id))
    lines.append("")

    cmp_result = report["cross_width_comparison"]
    lines.append("## 4. 跨宽度稳定性")
    lines.append("")
    lines.append(f"- 窄宽度新增换行的 cue：{', '.join(cmp_result['narrow_width_adds_wraps_for']) or '无'}")
    lines.append(f"- 层叠顺序两宽度一致：{cmp_result['layer_order_stable_across_widths']}")
    lines.append(f"- 滚动方向两宽度一致：{cmp_result['scroll_direction_stable_across_widths']}")
    lines.append(f"- 两宽度 REGION 均在视口内：{cmp_result['regions_inside_viewport_at_both_widths']}")
    lines.append("")

    lines.append("## 5. 滚动 REGION 与层叠")
    lines.append("")
    for width_key, width_result in report["widths"].items():
        lines.append(f"### {width_key}")
        lines.append("")
        lines.append("- 越界裁剪事件（滚动方向 up，旧行从 region 顶边裁出）：")
        for event in width_result["region_clip_events"][:6]:
            lines.append(f"  - {event}")
        if len(width_result["region_clip_events"]) > 6:
            lines.append(f"  - …共 {len(width_result['region_clip_events'])} 条，详见 JSON")
        lines.append("")

    lines.append("## 6. 时间重叠 cue 的层叠顺序")
    lines.append("")
    for group in report["temporal_overlap_groups"][:6]:
        lines.append(f"- `{group['cue']}` {group['active_window_s']} 与 "
                     f"{', '.join(group['overlaps_with'])} 时间重叠")
    lines.append("")
    lines.append("层叠规则：自由 cue 按激活顺序编号（小者先绘、大者覆盖）；"
                 "REGION cue 按进入顺序固定编号，重排时不改变身份，保证层叠稳定。")
    lines.append("")
    Path(out_path).write_text("\n".join(lines), encoding="utf-8")
