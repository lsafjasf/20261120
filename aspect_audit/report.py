from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from .models import LAYOUT_LABELS


ZH_LAYOUT = {
    "full": "满幅 16:9",
    "letterbox": "信箱黑边",
    "pillarbox": "柱状黑边",
    "fourside": "四边留黑",
    "black": "整帧黑场",
    "fade": "淡入/低亮",
    "unknown": "未判定",
}


def _segment_payload(segment) -> dict:
    payload = segment.as_dict()
    payload["label_zh"] = ZH_LAYOUT.get(segment.layout, segment.layout)
    return payload


def _event_payload(event) -> dict:
    payload = event.as_dict()
    payload["label"] = {
        "fade_in": "淡入",
        "full_black": "整帧黑场",
        "subtitle_in_black_bar": "字幕进入黑边",
    }.get(event.kind, event.kind)
    return payload


def build_report(analysis: dict, comparison: dict, source: str) -> dict:
    frames = analysis["frames"]
    summary_frames = {
        layout: sum(1 for frame in frames if frame.layout == layout)
        for layout in ZH_LAYOUT
    }
    truth = comparison.get("truth_validation")
    switch_errors = (
        [item["error_frames"] for item in truth["switch_checks"]]
        if truth else []
    )
    fp_intervals = (
        truth.get("false_positive_intervals", []) if truth else []
    )
    report = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "video": analysis["video"],
        "summary": {
            "stable_aspect_segments": len(analysis["segments"]),
            "detected_switches": len(analysis["switches"]),
            "full_black_events": len(analysis["events"]["black"]),
            "fade_events": len(analysis["events"]["fade"]),
            "subtitle_in_bar_events": len(analysis["events"]["subtitle"]),
            "declared_mismatch_intervals": len(comparison.get("mismatches", [])),
            "truth_switch_errors_frames": switch_errors,
            "false_positive_intervals": len(fp_intervals),
            "false_positive_frames": sum(
                item.get("frames", 0) for item in fp_intervals
            ),
            "layout_frame_counts": summary_frames,
        },
        "segments": [_segment_payload(item) for item in analysis["segments"]],
        "switches": analysis["switches"],
        "events": {
            kind: [_event_payload(item) for item in events]
            for kind, events in analysis["events"].items()
        },
        "declared_comparison": {
            "declared_schedule": comparison.get("declared_schedule"),
            "mismatches": comparison.get("mismatches", []),
        },
        "truth_validation": truth,
        "frame_results": [frame.as_dict() for frame in frames],
    }
    return report


def write_frame_csv(path: Path, frames: list) -> None:
    fields = [
        "frame", "time", "layout", "label_zh", "aspect", "confidence",
        "rect_x", "rect_y", "rect_width", "rect_height",
        "bar_top", "bar_bottom", "bar_left", "bar_right",
        "bar_types", "brightness", "scene_brightness", "dark_ratio",
        "subtitle_edges", "subtitle_confidence", "note",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for frame in frames:
            writer.writerow(
                {
                    "frame": frame.frame,
                    "time": f"{frame.time:.6f}",
                    "layout": frame.layout,
                    "label_zh": ZH_LAYOUT.get(frame.layout, frame.layout),
                    "aspect": f"{frame.aspect:.6f}",
                    "confidence": f"{frame.confidence:.6f}",
                    "rect_x": frame.rect.x,
                    "rect_y": frame.rect.y,
                    "rect_width": frame.rect.width,
                    "rect_height": frame.rect.height,
                    "bar_top": frame.bars["top"],
                    "bar_bottom": frame.bars["bottom"],
                    "bar_left": frame.bars["left"],
                    "bar_right": frame.bars["right"],
                    "bar_types": "|".join(frame.bar_types),
                    "brightness": f"{frame.brightness:.3f}",
                    "scene_brightness": f"{frame.scene_brightness:.3f}",
                    "dark_ratio": f"{frame.dark_ratio:.6f}",
                    "subtitle_edges": "|".join(frame.subtitle_edges),
                    "subtitle_confidence": f"{frame.subtitle_confidence:.6f}",
                    "note": frame.note,
                }
            )


def write_timeline(
    path: Path,
    analysis: dict,
    comparison: dict,
) -> None:
    width, height = 1500, 430
    left, right = 155, 30
    track_width = width - left - right
    frame_count = max(1, analysis["video"]["decoded_frames"])
    fps = max(0.001, analysis["video"]["fps"])
    image = Image.new("RGB", (width, height), (24, 28, 33))
    draw = ImageDraw.Draw(image)
    font_path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    font = (
        ImageFont.truetype(str(font_path), 15)
        if font_path.exists() else ImageFont.load_default()
    )
    small = (
        ImageFont.truetype(str(font_path), 12)
        if font_path.exists() else ImageFont.load_default()
    )
    draw.text(
        (left, 16),
        f"Aspect / active-area timeline  -  {Path(analysis['video'].get('path', 'video')).name}",
        fill=(232, 236, 240), font=font,
    )
    tracks = [
        ("layout", 62),
        ("black", 108),
        ("subtitle", 146),
        ("mismatch", 184),
        ("switch", 222),
    ]
    for label, y in tracks:
        draw.text((18, y + 6), label.upper(), fill=(175, 183, 192), font=small)
        draw.rectangle(
            (left, y, left + track_width, y + 26),
            fill=(38, 44, 51), outline=(65, 73, 82),
        )

    def x_for(frame: int) -> float:
        return left + track_width * frame / frame_count

    colors = {
        "full": (55, 166, 98),
        "letterbox": (232, 171, 62),
        "pillarbox": (65, 145, 215),
        "fourside": (155, 105, 215),
        "black": (8, 9, 10),
        "fade": (118, 124, 132),
        "unknown": (82, 88, 95),
    }
    for segment in analysis["segments"]:
        x0 = x_for(segment.start_frame)
        x1 = x_for(segment.end_frame + 1)
        draw.rectangle((x0, 62, x1, 88), fill=colors.get(segment.layout, colors["unknown"]))
    for event in analysis["events"]["black"]:
        draw.rectangle(
            (x_for(event.start_frame), 108, x_for(event.end_frame + 1), 134),
            fill=colors["black"], outline=(230, 100, 90),
        )
    for event in analysis["events"]["fade"]:
        draw.rectangle(
            (x_for(event.start_frame), 62, x_for(event.end_frame + 1), 88),
            fill=colors["fade"],
        )
    for event in analysis["events"]["subtitle"]:
        draw.rectangle(
            (x_for(event.start_frame), 146, x_for(event.end_frame + 1), 172),
            fill=(68, 201, 207),
        )
    for mismatch in comparison.get("mismatches", []):
        draw.rectangle(
            (x_for(mismatch["start_frame"]), 184, x_for(mismatch["end_frame"] + 1), 210),
            fill=(220, 68, 64),
        )
    for switch in analysis["switches"]:
        x = x_for(switch["frame"])
        draw.line((x, 222, x, 286), fill=(255, 92, 70), width=2)
        draw.polygon(
            [(x, 288), (x + 6, 300), (x - 6, 300)], fill=(255, 92, 70)
        )
        draw.text(
            (x + 9, 258), f"F{switch['frame']}", fill=(255, 148, 122), font=small
        )
    seconds = frame_count / fps
    step = 1 if seconds <= 12 else max(1, int(seconds / 10))
    tick = 0
    while tick / fps <= seconds + 0.001:
        frame = int(round(tick * fps))
        if frame > frame_count:
            break
        x = x_for(frame)
        draw.line((x, 310, x, 317), fill=(130, 138, 148), width=1)
        draw.text((x - 8, 322), f"{tick}s", fill=(150, 158, 168), font=small)
        tick += step
    legend = [
        ("full", "full"),
        ("letterbox", "letterbox"),
        ("pillarbox", "pillarbox"),
        ("fourside", "four-side black"),
        ("fade", "fade/low-light"),
    ]
    x = left
    for key, label in legend:
        draw.rectangle((x, 372, x + 18, 388), fill=colors[key])
        draw.text((x + 24, 371), label, fill=(185, 192, 201), font=small)
        x += 230
    image.save(path)


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(value) for value in row) + " |")
    return "\n".join(lines)


def write_markdown(
    path: Path,
    report: dict,
    frame_csv_name: str,
    timeline_name: str,
) -> None:
    video = report["video"]
    comparison = report["declared_comparison"]
    truth = report["truth_validation"]
    summary = report["summary"]
    lines = [
        f"# 电视母版画幅审计报告 — {Path(report['source']).stem}",
        "",
        "## 结论",
        "",
        f"- 检测到 {summary['stable_aspect_segments']} 个稳定画幅时间段、"
        f"{summary['detected_switches']} 次画幅切换。",
        f"- 整帧黑场 {summary['full_black_events']} 段、淡入 "
        f"{summary['fade_events']} 段、字幕进入黑边 "
        f"{summary['subtitle_in_bar_events']} 段。",
        f"- 与编辑方声明不一致区间：{summary['declared_mismatch_intervals']} 段。",
    ]
    if truth:
        errors = summary["truth_switch_errors_frames"]
        lines.append(
            f"- 真值切换帧误差：{errors}；误报区间："
            f"{summary['false_positive_intervals']} 段"
            f"（{summary['false_positive_frames']} 帧）。"
        )
    lines.extend(
        [
            "",
            "## 视频信息",
            "",
            _table(
                ["项目", "值"],
                [
                    ["文件", f"`{report['source']}`"],
                    ["画布", f"{video['width']}×{video['height']}"],
                    ["帧率", f"{video['fps']:.3f} fps"],
                    ["解码帧数", video["decoded_frames"]],
                    ["时长", f"{video['duration']:.3f} s"],
                    ["编码", f"{video.get('codec')} / {video.get('pix_fmt')}"],
                    ["逐帧表", f"`{frame_csv_name}`"],
                    ["时间线图", f"`{timeline_name}`"],
                ],
            ),
            "",
            f"![timeline]({timeline_name})",
            "",
            "## 检测画幅时间段",
            "",
        ]
    )
    segment_rows = []
    for item in report["segments"]:
        rect = item["rect"]
        segment_rows.append(
            [
                f"{item['start_frame']}–{item['end_frame']}",
                f"{item['start_time']:.3f}–{item['end_time']:.3f}",
                item["label_zh"],
                f"{item['aspect']:.3f}:1 ({item['canonical_aspect']})",
                f"x={rect['x']}, y={rect['y']}, "
                f"{rect['width']}×{rect['height']}",
                f"{item['confidence']:.3f}",
            ]
        )
    lines.append(
        _table(
            ["帧", "时间(s)", "类型", "有效画幅", "有效区域", "置信度"],
            segment_rows or [["—", "—", "未发现稳定画幅", "—", "—", "—"]],
        )
    )
    lines.extend(["", "## 特殊事件", ""])
    event_rows = []
    for kind, events in report["events"].items():
        for item in events:
            event_rows.append(
                [
                    item["label"],
                    f"{item['start_frame']}–{item['end_frame']}",
                    f"{item['start_time']:.3f}–{item['end_time']:.3f}",
                    item.get("edge", "—"),
                    f"{item['confidence']:.3f}",
                    item["detail"],
                ]
            )
    event_rows.sort(key=lambda row: float(str(row[2]).split("–")[0]))
    lines.append(
        _table(
            ["事件", "帧", "时间(s)", "边", "置信度", "说明"],
            event_rows or [["—", "—", "—", "—", "—", "未检测到"]],
        )
    )
    lines.extend(["", "## 与编辑方声明对拍", ""])
    if comparison.get("declared_schedule"):
        lines.append(
            f"声明文件：`{comparison['declared_schedule']['path']}`"
        )
    mismatch_rows = [
        [
            f"{item['start_frame']}–{item['end_frame']}",
            f"{item['start_time']:.3f}–{item['end_time']:.3f}",
            f"{item['detected_aspect']:.3f}:1",
            f"{item['declared_aspect']:.3f}:1",
            item["reason"],
        ]
        for item in comparison.get("mismatches", [])
    ]
    lines.append(
        _table(
            ["帧", "时间(s)", "检测", "声明", "原因"],
            mismatch_rows or [["—", "—", "—", "—", "无不一致"]],
        )
    )
    if truth:
        lines.extend(["", "## 真值验证", ""])
        switch_rows = [
            [
                item["truth_frame"],
                item["detected_frame"] if item["detected_frame"] is not None else "未匹配",
                item["error_frames"] if item["error_frames"] is not None else "—",
                item["error_seconds"] if item["error_seconds"] is not None else "—",
                item["confidence"] if item["confidence"] is not None else "—",
            ]
            for item in truth["switch_checks"]
        ]
        lines.append(
            _table(
                ["真值切换帧", "检测切换帧", "误差(帧)", "误差(s)", "置信度"],
                switch_rows or [["—", "—", "—", "—", "—"]],
            )
        )
        fp_rows = [
            [
                f"{item['start_frame']}–{item['end_frame']}",
                f"{item['start_time']:.3f}–{item['end_time']:.3f}",
                item["frames"],
                item["reason"],
            ]
            for item in truth["false_positive_intervals"]
        ]
        lines.extend(
            [
                "",
                "**误报区间**",
                "",
                _table(
                    ["帧", "时间(s)", "帧数", "原因"],
                    fp_rows or [["无", "—", 0, "未发现画幅误报"]],
                ),
            ]
        )
        event_check_rows = []
        for kind, item in truth["event_checks"].items():
            if item is None:
                continue
            event_check_rows.append(
                [
                    {
                        "full_black": "整帧黑场",
                        "subtitle_in_black_bar": "字幕入黑边",
                        "fade_in": "淡入",
                    }.get(kind, kind),
                    f"{item['truth_start_frame']}–{item['truth_end_frame']}",
                    (
                        f"{item['detected_start_frame']}–{item['detected_end_frame']}"
                        if item["detected_start_frame"] is not None else "未匹配"
                    ),
                    f"{item['recall']:.3f}",
                    item["confidence"] if item["confidence"] is not None else "—",
                ]
            )
        lines.extend(
            [
                "",
                _table(
                    ["事件", "真值帧", "检测帧", "召回", "置信度"],
                    event_check_rows,
                ),
            ]
        )
    lines.extend(
        [
            "",
            "## 判定说明",
            "",
            "- 有效区域由四条边缘向内的低亮连续带、边界亮度台阶和跨帧边界中值共同确定。",
            "- 短亮异常（包括字幕笔画）允许被跨过，但必须在其后重新出现连续黑带，避免字幕截断黑边。",
            "- 低亮度帧和整帧黑场不参与画幅切换投票；淡入与片内黑场分别记录置信度。",
            "- 字幕检测仅在黑边内部搜索高亮连通区域，不把画面内字幕当作黑边事件。",
            "- 声明对拍仅在稳定画幅帧上执行；黑场、淡入和未判定帧不产生画幅不一致告警。",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def write_reports(
    output_dir: str | Path,
    stem: str,
    analysis: dict,
    comparison: dict,
) -> dict:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    report = build_report(analysis, comparison, analysis["video"]["path"])
    json_path = output / f"{stem}_report.json"
    markdown_path = output / f"{stem}_report.md"
    csv_path = output / f"{stem}_frames.csv"
    timeline_path = output / f"{stem}_timeline.png"
    write_frame_csv(csv_path, analysis["frames"])
    write_timeline(timeline_path, analysis, comparison)
    write_markdown(
        markdown_path, report, csv_path.name, timeline_path.name
    )
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "json": str(json_path),
        "markdown": str(markdown_path),
        "frames_csv": str(csv_path),
        "timeline": str(timeline_path),
    }
