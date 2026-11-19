"""Build the cross-width layout verification report (JSON + Markdown)."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .engine import LayoutEngine
from .framerate import check_frame_rate, extract_script_tag
from .parser import parse_webvtt


WIDTHS = ((1280, 720), (640, 360))
SAMPLE_TIMES = [0.0, 0.5, 1.0, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 6.5,
                7.0, 8.0, 9.0, 10.0]


def box_dict(layout) -> dict:
    return {
        "x": round(layout.box.x, 2),
        "y": round(layout.box.y, 2),
        "width": round(layout.box.width, 2),
        "height": round(layout.box.height, 2),
        "right": round(layout.box.right, 2),
        "bottom": round(layout.box.bottom, 2),
    }


def break_dump(layout) -> list[dict]:
    return [
        {
            "text": line.text,
            "display_width_px": round(line.width_px, 2),
            "break_before_char_index": line.break_before_index,
            "hard_break": line.hard,
            "timestamp_tag_s": line.timestamp,
        }
        for line in layout.rendered_lines
    ]


def temporal_overlaps(cues) -> list[dict]:
    groups: list[dict] = []
    ordered = sorted(cues, key=lambda cue: (cue.start, cue.end))
    for idx, cue in enumerate(ordered):
        peers = [
            other.id for other in ordered[idx + 1:]
            if other.start < cue.end and cue.start < other.end
        ]
        if peers:
            groups.append({"cue": cue.id,
                           "active_window_s": [cue.start, cue.end],
                           "overlaps_with": peers})
    return groups


def viewport_checks(width_result: dict, width: int, height: int) -> dict:
    violations = []
    for cue_id, entry in width_result["cues"].items():
        for box in entry["boxes"]:
            if box["x"] < 0 or box["y"] < 0 or box["right"] > width \
                    or box["bottom"] > height:
                violations.append({"cue": cue_id, "t": box["t"], "box": box})
    region_area = sum(
        box["width"] * box["height"]
        for box in width_result["region_boxes"]
        if box["t"] == width_result["region_boxes"][0]["t"]
    )
    return {
        "cue_viewport_violations": violations,
        "region_footprint_px2": round(region_area, 2),
        "region_footprint_pct_of_viewport": round(
            100 * region_area / (width * height), 2),
        "all_regions_inside_viewport": all(
            box["inside_viewport"] for box in width_result["region_boxes"]),
    }


def compare_widths(wide: dict, narrow: dict) -> dict:
    cue_ids = sorted(set(wide["cues"]) & set(narrow["cues"]))
    break_counts = {
        cue_id: {
            "wide_lines": len(wide["cues"][cue_id]["line_breaks"]),
            "narrow_lines": len(narrow["cues"][cue_id]["line_breaks"]),
        }
        for cue_id in cue_ids
    }
    layer_stable = all(
        wide["cues"][cue_id]["layer_order"]
        == narrow["cues"][cue_id]["layer_order"]
        for cue_id in cue_ids
    )
    scroll_stable = all(
        wide["cues"][cue_id]["scroll"] == narrow["cues"][cue_id]["scroll"]
        for cue_id in cue_ids
    )
    region_ids = {box["region"] for box in wide["region_boxes"]}
    region_ok = all(
        all(box["inside_viewport"]
            for box in result["region_boxes"]
            if box["region"] == region_id)
        for result in (wide, narrow) for region_id in region_ids
    )
    return {
        "break_counts_per_cue": break_counts,
        "narrow_width_adds_wraps_for": [
            cue_id for cue_id, counts in break_counts.items()
            if counts["narrow_lines"] > counts["wide_lines"]
        ],
        "layer_order_stable_across_widths": layer_stable,
        "scroll_direction_stable_across_widths": scroll_stable,
        "regions_inside_viewport_at_both_widths": region_ok,
        "geometric_overlap_events": {
            "wide": wide["overlap_events"],
            "narrow": narrow["overlap_events"],
        },
    }


def run_width(doc, width: int, height: int) -> dict:
    engine = LayoutEngine(doc, width, height)
    snapshots = engine.simulate(SAMPLE_TIMES)
    cue_report: dict[str, dict] = {}
    for snapshot in snapshots:
        for layout in snapshot.cue_layouts:
            entry = cue_report.setdefault(layout.cue.id, {
                "region": layout.region_id,
                "scroll": layout.scroll_direction,
                "layer_order": layout.layer,
                "first_seen_s": snapshot.time,
                "line_breaks": break_dump(layout),
                "boxes": [],
            })
            entry["boxes"].append({"t": snapshot.time, **box_dict(layout)})

    overlap_events = [
        {"t": snapshot.time, "pair": list(pair)}
        for snapshot in snapshots for pair in snapshot.overlaps
    ]
    clip_events = [
        event for snapshot in snapshots
        for event in snapshot.region_clip_events
    ]
    regions = []
    for snapshot in snapshots:
        for region_layout in snapshot.region_layouts:
            regions.append({
                "t": snapshot.time,
                "region": region_layout.region.id,
                **box_dict(region_layout),
                "inside_viewport": region_layout.inside_viewport,
            })
    return {
        "viewport": {"width": width, "height": height,
                     "font_px": round(engine.font_px, 3),
                     "line_height_px": round(engine.line_height_px, 3)},
        "cues": cue_report,
        "overlap_events": overlap_events,
        "region_clip_events": clip_events,
        "region_boxes": regions,
    }


def build(vtt_path: str, html_path: str, out_dir: str) -> dict:
    doc = parse_webvtt(Path(vtt_path).read_text(encoding="utf-8"))
    html_text = Path(html_path).read_text(encoding="utf-8")
    fps_verdict = check_frame_rate(html_text)
    _, embedded = extract_script_tag(html_text)
    embedded_doc = parse_webvtt(embedded)

    width_results: dict[str, dict] = {}
    for width, height in WIDTHS:
        result = run_width(doc, width, height)
        result["viewport_checks"] = viewport_checks(result, width, height)
        width_results[f"{width}x{height}"] = result

    report = {
        "source": {"vtt": vtt_path, "html": html_path},
        "cue_count": len(doc.cues),
        "temporal_overlap_groups": temporal_overlaps(doc.cues),
        "regions": {rid: asdict(region) for rid, region in doc.regions.items()},
        "styles": [asdict(rule) for rule in doc.styles],
        "widths": width_results,
        "cross_width_comparison": compare_widths(
            width_results["1280x720"], width_results["640x360"]),
        "frame_rate": asdict(fps_verdict),
        "embedded_cue_count": len(embedded_doc.cues),
    }
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "layout_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
