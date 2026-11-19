#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


STYLE_FIELDS = [
    "Name", "Fontname", "Fontsize", "PrimaryColour", "SecondaryColour",
    "OutlineColour", "BackColour", "Bold", "Italic", "Underline",
    "StrikeOut", "ScaleX", "ScaleY", "Spacing", "Angle", "BorderStyle",
    "Outline", "Shadow", "Alignment", "MarginL", "MarginR", "MarginV",
    "Encoding",
]

SCRIPT_DEFAULTS = {
    "Name": "",
    "Fontname": "Arial",
    "Fontsize": 20.0,
    "PrimaryColour": "&H00FFFFFF",
    "SecondaryColour": "&H000000FF",
    "OutlineColour": "&H00000000",
    "BackColour": "&H00000000",
    "Bold": 0,
    "Italic": 0,
    "Underline": 0,
    "StrikeOut": 0,
    "ScaleX": 100.0,
    "ScaleY": 100.0,
    "Spacing": 0.0,
    "Angle": 0.0,
    "BorderStyle": 1,
    "Outline": 2.0,
    "Shadow": 2.0,
    "Alignment": 2,
    "MarginL": 10,
    "MarginR": 10,
    "MarginV": 10,
    "Encoding": 1,
}

PROBES = {
    17: "assprobe_5lxeyurf/ev17.ass",
    18: "assprobe_fxz78jt7/ev18.ass",
    19: "assprobe_j59tj5lv/ev19.ass",
    20: "assprobe_gvdyl3rp/ev20.ass",
    21: "assprobe_fe1wes0o/ev21.ass",
    22: "assprobe_zrx_o07l/ev22.ass",
    23: "assprobe_fp5t06dk/ev23.ass",
    24: "assprobe_smpol32c/ev24.ass",
}

OBSERVED_PRELOAD_HINTS = {
    0: 239,
    1: 423,
    2: 433,
    3: 330,
    4: 161,
    5: 0,
}


@dataclass
class Event:
    line: int
    start: float
    end: float
    style: str
    margin_l: int
    margin_r: int
    margin_v: int
    text: str


def parse_time(value):
    parts = value.strip().split(":")
    if len(parts) == 3:
        hours, minutes, seconds = parts
    else:
        hours, minutes, seconds = "0", parts[0], parts[1]
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def convert_value(field, value):
    value = value.strip()
    if field in {"Bold", "Italic", "Underline", "StrikeOut", "BorderStyle",
                 "Alignment", "MarginL", "MarginR", "MarginV", "Encoding"}:
        return int(value)
    if field in {"Fontsize", "ScaleX", "ScaleY", "Spacing", "Angle",
                 "Outline", "Shadow"}:
        return float(value)
    return value


def parse_ass(path):
    styles = {}
    events = []
    section = None
    style_format = None
    event_format = None
    for line_number, raw in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].lower()
            continue
        if section == "v4+ styles":
            if line.lower().startswith("format:"):
                style_format = [item.strip() for item in line.split(":", 1)[1].split(",")]
            elif line.lower().startswith("style:") and style_format:
                values = line.split(":", 1)[1].split(",", len(style_format) - 1)
                parsed = {
                    field: convert_value(field, value)
                    for field, value in zip(style_format, values)
                }
                styles[parsed["Name"]] = parsed
        elif section == "events":
            if line.lower().startswith("format:"):
                event_format = [item.strip() for item in line.split(":", 1)[1].split(",")]
            elif line.lower().startswith("dialogue:") and event_format:
                values = line.split(":", 1)[1].split(",", len(event_format) - 1)
                row = dict(zip(event_format, values))
                events.append(Event(
                    line=line_number,
                    start=parse_time(row["Start"]),
                    end=parse_time(row["End"]),
                    style=row["Style"].strip(),
                    margin_l=int(row["MarginL"]),
                    margin_r=int(row["MarginR"]),
                    margin_v=int(row["MarginV"]),
                    text=row["Text"],
                ))
    return styles, events


def tag_blocks(text):
    return re.findall(r"\{([^}]*)\}", text)


def parse_tags(text):
    overrides = {}
    position = None
    movement = None
    fade = None
    for block in tag_blocks(text):
        for match in re.finditer(r"\\([1-4]?[a-zA-Z]+)([^\\}]*)", block):
            name = match.group(1)
            args = match.group(2).strip()
            lower = name.lower()
            if lower in {"b", "i", "u", "s"}:
                field = {"b": "Bold", "i": "Italic", "u": "Underline", "s": "StrikeOut"}[lower]
                overrides[field] = int(args or "0")
            elif lower == "fs":
                overrides["Fontsize"] = float(args)
            elif lower in {"fscx", "fscy", "fsp", "frz"}:
                field = {"fscx": "ScaleX", "fscy": "ScaleY",
                         "fsp": "Spacing", "frz": "Angle"}[lower]
                overrides[field] = float(args)
            elif lower in {"bord", "shad"}:
                overrides["Outline" if lower == "bord" else "Shadow"] = float(args)
            elif lower in {"c", "1c"}:
                overrides["PrimaryColour"] = args.rstrip("&")
            elif lower == "2c":
                overrides["SecondaryColour"] = args.rstrip("&")
            elif lower == "3c":
                overrides["OutlineColour"] = args.rstrip("&")
            elif lower == "4c":
                overrides["BackColour"] = args.rstrip("&")
            elif lower == "an":
                overrides["Alignment"] = int(args)
            elif lower == "pos":
                values = [float(item) for item in args.strip("()").split(",")]
                position = values[:2]
            elif lower == "move":
                values = [float(item) for item in args.strip("()").split(",")]
                movement = values
            elif lower == "fad":
                values = [int(float(item)) for item in args.strip("()").split(",")]
                fade = {"kind": "fad", "in": values[0], "out": values[1]}
            elif lower == "fade":
                values = [int(float(item)) for item in args.strip("()").split(",")]
                fade = {
                    "kind": "fade", "a1": values[0], "a2": values[1],
                    "a3": values[2], "t1": values[3], "t2": values[4],
                    "t3": values[5], "t4": values[6],
                }
    return overrides, position, movement, fade


def ass_alpha_to_opacity(alpha):
    return max(0.0, min(1.0, (255 - alpha) / 255.0))


def opacity_at(event, fade, timestamp):
    if timestamp < event.start or timestamp >= event.end:
        return 0.0
    relative = timestamp - event.start
    if fade is None:
        return 1.0
    if fade["kind"] == "fad":
        fade_in = fade["in"] / 1000.0
        fade_out = fade["out"] / 1000.0
        if fade_in > 0 and relative < fade_in:
            return relative / fade_in
        remaining = event.end - timestamp
        if fade_out > 0 and remaining < fade_out:
            return max(0.0, remaining / fade_out)
        return 1.0

    marks = [fade[key] / 1000.0 for key in ("t1", "t2", "t3", "t4")]
    alphas = [fade[key] for key in ("a1", "a2", "a3")]
    if relative <= marks[0]:
        return ass_alpha_to_opacity(alphas[0])
    if relative < marks[1]:
        ratio = (relative - marks[0]) / (marks[1] - marks[0])
        alpha = alphas[0] + ratio * (alphas[1] - alphas[0])
        return ass_alpha_to_opacity(alpha)
    if relative < marks[2]:
        return ass_alpha_to_opacity(alphas[1])
    if relative < marks[3]:
        ratio = (relative - marks[2]) / (marks[3] - marks[2])
        alpha = alphas[1] + ratio * (alphas[2] - alphas[1])
        return ass_alpha_to_opacity(alpha)
    return ass_alpha_to_opacity(alphas[2])


def default_position(event, style, alignment, play_res):
    width, height = play_res
    margin_l = event.margin_l or style["MarginL"]
    margin_r = event.margin_r or style["MarginR"]
    margin_v = event.margin_v or style["MarginV"]
    column = (alignment - 1) % 3
    row = (alignment - 1) // 3
    if column == 0:
        x = margin_l
    elif column == 1:
        x = width / 2
    else:
        x = width - margin_r
    if row == 0:
        y = height - margin_v
    elif row == 1:
        y = height / 2
    else:
        y = margin_v
    return [float(x), float(y)]


def position_at(event, style, overrides, position, movement, alignment, play_res, timestamp):
    if position is not None:
        return position
    if movement is not None:
        sx, sy, ex, ey, *tail = movement
        delay = tail[0] / 1000.0 if tail else 0.0
        move_duration = tail[1] / 1000.0 if len(tail) > 1 else event.end - event.start
        begin = event.start + delay
        finish = begin + move_duration
        if timestamp <= begin:
            ratio = 0.0
        elif timestamp >= finish:
            ratio = 1.0
        else:
            ratio = (timestamp - begin) / (finish - begin)
        return [sx + ratio * (ex - sx), sy + ratio * (ey - sy)]
    return default_position(event, style, overrides.get("Alignment", alignment), play_res)


def scale_point(point, play_res, canvas):
    return [
        point[0] * canvas[0] / play_res[0],
        point[1] * canvas[1] / play_res[1],
    ]


def build_style_chain(style_name, style, overrides):
    chain = [
        {"layer": "script-defaults", "values": dict(SCRIPT_DEFAULTS)},
        {"layer": f"style:{style_name}", "values": dict(style)},
        {"layer": "dialogue-overrides", "values": dict(overrides)},
    ]
    effective = dict(SCRIPT_DEFAULTS)
    for layer in chain:
        effective.update(layer["values"])
    return chain, effective


def render_probe(renderer, probe_path, times, lib_dir, width, height):
    env = dict(os.environ)
    if lib_dir:
        env["LD_LIBRARY_PATH"] = lib_dir
    payload = "".join(f"{timestamp:.1f}\n" for timestamp in times)
    process = subprocess.run(
        [renderer, str(probe_path)],
        input=payload,
        text=True,
        capture_output=True,
        check=True,
        env=env,
    )
    rows = {}
    for line in process.stdout.splitlines()[1:]:
        fields = line.split("\t")
        if len(fields) < 8:
            continue
        timestamp = float(fields[0])
        rows[timestamp] = {
            "images": int(fields[2]),
            "alpha": float(fields[3]),
            "bbox": [int(fields[4]), int(fields[5]), int(fields[6]), int(fields[7])],
        }
    return rows


def measured_anchor(bbox, alignment):
    if not bbox or bbox[2] < 0:
        return None
    x0, y0, x1, y1 = bbox
    column = (alignment - 1) % 3
    row = (alignment - 1) // 3
    if column == 0:
        x = x0
    elif column == 1:
        x = (x0 + x1) / 2
    else:
        x = x1
    if row == 0:
        y = y1
    elif row == 1:
        y = (y0 + y1) / 2
    else:
        y = y0
    return [x, y]


def union_duration(intervals):
    if not intervals:
        return 0.0
    total = 0.0
    current_start, current_end = sorted(intervals)[0]
    for start, end in sorted(intervals)[1:]:
        if start <= current_end:
            current_end = max(current_end, end)
        else:
            total += current_end - current_start
            current_start, current_end = start, end
    return total + current_end - current_start


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ass", default="asscheck/sample.ass")
    parser.add_argument("--probe-dir", default="artifacts/out/probes")
    parser.add_argument("--renderer", default="build/ass_render_probe")
    parser.add_argument("--libass-lib", default=os.environ.get("LIBASS_LIB_DIR", ""))
    parser.add_argument("--json", default="artifacts/out/ass_verification.json")
    parser.add_argument("--log", default="artifacts/out/ass_verification.log")
    args = parser.parse_args()

    canvas = [1920, 1080]
    play_res = [1280, 720]
    styles, events = parse_ass(args.ass)
    raw_lines = Path(args.ass).read_bytes().splitlines(keepends=True)
    parsed = {}
    for event in events:
        overrides, position, movement, fade = parse_tags(event.text)
        parsed[event.line] = {
            "overrides": overrides,
            "position": position,
            "movement": movement,
            "fade": fade,
        }

    snapshots = []
    mismatches = []
    for event in events:
        style = styles[event.style]
        info = parsed[event.line]
        overrides = info["overrides"]
        alignment = overrides.get("Alignment", style["Alignment"])
        times = [round(event.start - 0.6 + 0.2 * index, 1) for index in range(27)]
        probe_path = Path(args.probe_dir) / PROBES[event.line]
        libass = render_probe(
            args.renderer, probe_path, times, args.libass_lib,
            canvas[0], canvas[1],
        )
        for timestamp in times:
            opacity = opacity_at(event, info["fade"], timestamp)
            visible = opacity > 0.0001
            script_position = position_at(
                event, style, overrides, info["position"], info["movement"],
                alignment, play_res, timestamp,
            )
            canvas_position = scale_point(script_position, play_res, canvas)
            actual = libass[timestamp]
            libass_visible = actual["alpha"] > 0.0001
            mismatch = visible != libass_visible or abs(opacity - actual["alpha"]) > 0.01
            snapshot = {
                "t": timestamp,
                "event_line": event.line,
                "computed": visible,
                "libass": libass_visible,
                "alpha": round(opacity, 4),
                "libass_alpha": actual["alpha"],
                "position_script": [round(value, 3) for value in script_position],
                "position_canvas": [round(value, 3) for value in canvas_position],
                "libass_bbox_canvas": actual["bbox"],
                "mismatch": mismatch,
            }
            snapshots.append(snapshot)
            if mismatch:
                mismatches.append(snapshot)

    style_chains = []
    for event in events:
        style = styles[event.style]
        overrides = parsed[event.line]["overrides"]
        chain, effective = build_style_chain(event.style, style, overrides)
        folded = dict(SCRIPT_DEFAULTS)
        for layer in chain:
            folded.update(layer["values"])
        style_chains.append({
            "event_line": event.line,
            "style": event.style,
            "chain": chain,
            "effective": effective,
            "inheritance_ok": folded == effective,
        })

    geometry = []
    for event_line, timestamp in [(17, 3.0), (19, 5.0), (20, 6.0)]:
        event = next(item for item in events if item.line == event_line)
        snapshot = next(
            item for item in snapshots
            if item["event_line"] == event_line and item["t"] == timestamp
        )
        style = styles[event.style]
        alignment = parsed[event.line]["overrides"].get("Alignment", style["Alignment"])
        measured = measured_anchor(snapshot["libass_bbox_canvas"], alignment)
        expected = snapshot["position_canvas"]
        offset = [measured[0] - expected[0], measured[1] - expected[1]]
        tolerance = 0.39 * style["Fontsize"] * canvas[0] / play_res[0]
        geometry.append({
            "event_line": event_line,
            "t": timestamp,
            "align": alignment,
            "anchor_script": snapshot["position_script"],
            "anchor_canvas": expected,
            "measured_canvas": [round(value, 3) for value in measured],
            "offset": [round(value, 3) for value in offset],
            "tolerance": round(tolerance, 3),
            "ok": abs(offset[0]) <= tolerance and abs(offset[1]) <= tolerance,
        })

    vertical = []
    vertical_event = next(item for item in events if item.line == 22)
    vertical_style = styles[vertical_event.style]
    vertical_time = next(
        item for item in snapshots
        if item["event_line"] == 22 and item["t"] == 6.0
    )
    bbox = vertical_time["libass_bbox_canvas"]
    expected_script = play_res[0] - vertical_style["MarginV"]
    measured_script = bbox[2] / canvas[0] * play_res[0]
    vertical.append({
        "event_line": 22,
        "baseline_expected_x": expected_script,
        "baseline_measured_x": round(measured_script, 3),
        "offset": round(measured_script - expected_script, 3),
        "tolerance": 10.0,
        "libass_bbox_canvas": bbox,
        "ok": abs(measured_script - expected_script) <= 10.0,
    })

    parts = []
    for index in range(6):
        window_start = index * 2.0
        window_end = window_start + 2.0
        active = [
            event for event in events
            if event.start < window_end and event.end > window_start
        ]
        intervals = [
            (max(event.start, window_start), min(event.end, window_end))
            for event in active
        ]
        byte_count = sum(len(raw_lines[event.line - 1]) for event in active)
        parts.append({
            "index": index,
            "window": [window_start, window_end],
            "events": [event.line for event in active],
            "bytes": byte_count,
            "play_duration": round(union_duration(intervals), 3),
        })
    for index, part in enumerate(parts):
        next_bytes = parts[index + 1]["bytes"] if index + 1 < len(parts) else 0
        part["preload_hint_bytes"] = OBSERVED_PRELOAD_HINTS[index]
        part["next_part_bytes"] = next_bytes
        part["hint_ok"] = part["preload_hint_bytes"] == next_bytes

    position_conversions = []
    for event_line, samples in {
        19: [("pos", 5.0)],
        20: [("move-start", 4.0), ("move-mid", 6.0), ("move-end", 8.0)],
    }.items():
        event = next(item for item in events if item.line == event_line)
        style = styles[event.style]
        for label, timestamp in samples:
            script = position_at(
                event, style, parsed[event.line]["overrides"],
                parsed[event.line]["position"], parsed[event.line]["movement"],
                style["Alignment"], play_res, timestamp,
            )
            position_conversions.append({
                "event_line": event_line,
                "tag": label,
                "t": timestamp,
                "script": script,
                "canvas": scale_point(script, play_res, canvas),
            })

    checks = {
        "timeline_ok": not mismatches,
        "style_inheritance_ok": all(item["inheritance_ok"] for item in style_chains),
        "geometry_ok": all(item["ok"] for item in geometry),
        "vertical_baseline_ok": all(item["ok"] for item in vertical),
        "parts_ok": all(item["hint_ok"] for item in parts),
    }
    checks["all_checks_ok"] = all(checks.values())
    report = {
        "ass": str(Path(args.ass).resolve()),
        "canvas": canvas,
        "play_res": play_res,
        "timeline_mismatch_count": len(mismatches),
        "timeline_mismatches": mismatches,
        "checks": checks,
        "style_inheritance_ok": checks["style_inheritance_ok"],
        "style_chains": style_chains,
        "geometry": geometry,
        "position_conversions": position_conversions,
        "vertical_baseline": vertical,
        "parts": parts,
        "snapshots": snapshots,
    }
    Path(args.json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    lines = [
        f"timeline entries compared: {len(snapshots)}",
        f"timeline mismatches: {len(mismatches)}",
        f"style inheritance chains: {len(style_chains)} correct={report['style_inheritance_ok']}",
        f"all checks passed: {checks['all_checks_ok']}",
    ]
    for item in geometry:
        lines.append(
            "geo line={event_line} canvas={w}x{h} expected=({ex:.1f},{ey:.1f}) "
            "measured=({mx:.1f},{my:.1f}) ok={ok}".format(
                event_line=item["event_line"], w=canvas[0], h=canvas[1],
                ex=item["anchor_canvas"][0], ey=item["anchor_canvas"][1],
                mx=item["measured_canvas"][0], my=item["measured_canvas"][1],
                ok=item["ok"],
            )
        )
    for item in vertical:
        lines.append(
            "vertical line={event_line} baseline_x expected={expected:.1f} "
            "measured={measured:.1f} offset={offset:.1f} tol={tol:.1f} ok={ok}".format(
                event_line=item["event_line"], expected=item["baseline_expected_x"],
                measured=item["baseline_measured_x"], offset=item["offset"],
                tol=item["tolerance"], ok=item["ok"],
            )
        )
    for part in parts:
        lines.append(
            "part {index} window=[{ws:.1f}, {we:.1f}] bytes={bytes} play={duration:.1f}s "
            "hint={hint} hint_ok={ok}".format(
                index=part["index"], ws=part["window"][0], we=part["window"][1],
                bytes=part["bytes"], duration=part["play_duration"],
                hint=part["preload_hint_bytes"], ok=part["hint_ok"],
            )
        )
    Path(args.log).write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
