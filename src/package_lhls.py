#!/usr/bin/env python3
import json
import math
import re
import statistics
import tempfile
from pathlib import Path

from media_utils import (
    ROOT, boxes, decode_first_frame_seconds, ffprobe_json, find_tool, parse_init,
    parse_nal_types, parse_tfdt, probe_frames, write_csv, write_json,
)

PART_SECONDS = 0.5
SEGMENT_PARTS = 4
SEGMENT_SECONDS = PART_SECONDS * SEGMENT_PARTS
NETWORK_MBPS = 16
START_TIMES = [2.1, 3.3, 5.7, 8.2, 10.4]


def split_media(source, out):
    data = source.read_bytes()
    init = parse_init(data)
    init["duration"] = float(
        ffprobe_json(source, ["format=duration"])["format"]["duration"]
    )
    top = list(boxes(data))
    moofs = [box for box in top if box.typ == "moof"]
    mdats = [box for box in top if box.typ == "mdat"]
    if len(moofs) != len(mdats):
        raise RuntimeError("unexpected moof/mdat layout")

    out.mkdir(parents=True, exist_ok=True)
    (out / "init.mp4").write_bytes(init["init_bytes"])
    parts = []
    for index, moof in enumerate(moofs):
        mdat = next((box for box in mdats if box.start > moof.start), None)
        if not mdat:
            raise RuntimeError(f"missing mdat after moof {index}")
        payload = data[moof.start:mdat.end]
        path = out / f"part-{index:03d}.m4s"
        path.write_bytes(payload)
        nal_types = parse_nal_types(
            data, moof, mdat, init["nal_length_size"]
        )
        vcl = [typ for typ in nal_types if typ in (1, 5)]
        parts.append({
            "index": index,
            "path": path,
            "size": len(payload),
            "tfdt": parse_tfdt(data, moof) / init["timescale"],
            "idr": bool(vcl and vcl[0] == 5 and 5 in nal_types),
            "nal_types": nal_types,
        })

    for index, part in enumerate(parts):
        if index + 1 < len(parts):
            part["duration"] = parts[index + 1]["tfdt"] - part["tfdt"]
        else:
            part["duration"] = init["duration"] - part["tfdt"]

    segments = []
    for index in range(0, len(parts), SEGMENT_PARTS):
        group = parts[index:index + SEGMENT_PARTS]
        payload = b"".join(part["path"].read_bytes() for part in group)
        path = out / f"segment-{index // SEGMENT_PARTS:03d}.m4s"
        path.write_bytes(payload)
        segments.append({
            "index": index // SEGMENT_PARTS,
            "path": path,
            "size": len(payload),
            "first_part": index,
            "last_part": index + len(group) - 1,
        })
    return init, parts, segments


def playlist(part_count, parts, segments, hint_index):
    media_sequence = hint_index // SEGMENT_PARTS
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:9",
        "#EXT-X-TARGETDURATION:2",
        f"#EXT-X-PART-INF:PART-TARGET={PART_SECONDS:.3f}",
        f"#EXT-X-MEDIA-SEQUENCE:{media_sequence}",
        "#EXT-X-SERVER-CONTROL:CAN-BLOCK-RELOAD=YES,PART-HOLD-BACK=1.500",
        "#EXT-X-INDEPENDENT-SEGMENTS",
        '#EXT-X-MAP:URI="init.mp4"',
    ]
    for segment in segments[:media_sequence]:
        lines += [
            f"#EXTINF:{SEGMENT_SECONDS:.3f},",
            f'segment-{segment["index"]:03d}.m4s',
        ]
    for part in parts[media_sequence * SEGMENT_PARTS:hint_index]:
        lines.append(
            f'#EXT-X-PART:DURATION={part["duration"]:.3f},'
            f'URI="part-{part["index"]:03d}.m4s",INDEPENDENT={"YES" if part["idr"] else "NO"}'
        )
    if hint_index < part_count:
        lines.append(
            f'#EXT-X-PRELOAD-HINT:TYPE=PART,URI="part-{hint_index:03d}.m4s"'
        )
    return "\n".join(lines) + "\n"


def parse_playlist(text):
    parts = []
    hints = []
    for line in text.splitlines():
        if line.startswith("#EXT-X-PART:"):
            attrs = dict(re.findall(r'([A-Z-]+)=("[^"]*"|[^,]*)', line[12:]))
            parts.append({k: v.strip('"') for k, v in attrs.items()})
        elif line.startswith("#EXT-X-PRELOAD-HINT:"):
            attrs = dict(re.findall(r'([A-Z-]+)=("[^"]*"|[^,]*)', line[20:]))
            hints.append({k: v.strip('"') for k, v in attrs.items()})
    return parts, hints


def validate(parts, segments, init, snapshot_texts, frames):
    starts = [part["tfdt"] for part in parts]
    key_times = [round(frame["pts"], 6) for frame in frames if frame["key"]]
    parsed = [parse_playlist(text) for text in snapshot_texts]
    hints = [item[1][0] for item in parsed]
    checks = {
        "part_count": len(parts),
        "segment_count": len(segments),
        "all_parts_0_5s": all(abs(p["duration"] - PART_SECONDS) < 1e-6 for p in parts),
        "all_segments_2s": all(
            abs(sum(p["duration"] for p in parts[s["first_part"]:s["last_part"] + 1])
                - SEGMENT_SECONDS) < 1e-6
            for s in segments
        ),
        "all_parts_idr": all(p["idr"] for p in parts),
        "all_listed_parts_valid": all(
            abs(float(part["DURATION"]) - PART_SECONDS) < 1e-6
            and part.get("INDEPENDENT") == "YES"
            for listed, _ in parsed for part in listed
        ),
        "all_part_starts_keyframes": all(
            any(abs(frame["pts"] - p["tfdt"]) < 1e-6 and frame["key"] for frame in frames)
            for p in parts
        ),
        "gop_equals_part_interval": (
            len(key_times) == len(parts)
            and all(abs(a - b) < 1e-6 for a, b in zip(key_times, starts))
        ),
        "hint_count": len(hints),
        "all_hints_last_part": all(
            hint["URI"] == f'part-{index:03d}.m4s'
            and hint["TYPE"] == "PART"
            and hint["URI"] not in {p["URI"] for p in listed}
            for index, ((listed, _), hint) in enumerate(zip(parsed, hints))
        ),
        "all_hint_starts_match": all(
            abs(parts[index]["tfdt"] - index * PART_SECONDS) < 1e-6
            for index in range(len(parts))
        ),
        "media_timescale": init["timescale"],
        "duration": init["duration"],
    }
    return checks


def simulate_latency(parts, segments, decode_part, decode_segment, out_dir):
    rows = []
    for start in START_TIMES:
        part_index = int(math.floor(start / PART_SECONDS))
        part = parts[part_index]
        part_release = (part_index + 1) * PART_SECONDS
        part_transfer = part["size"] * 8 / (NETWORK_MBPS * 1_000_000)
        part_display = part_release + part_transfer + decode_part
        rows.append({
            "mode": "part",
            "request_time_s": start,
            "media_start_s": part["tfdt"],
            "release_time_s": part_release,
            "bytes": part["size"],
            "transfer_s": part_transfer,
            "decode_first_frame_s": decode_part,
            "first_frame_display_s": part_display,
            "end_to_end_s": part_display - part["tfdt"],
            "request_to_first_frame_s": part_display - start,
        })
        segment_index = int(math.floor(start / SEGMENT_SECONDS))
        segment = segments[segment_index]
        segment_release = (segment_index + 1) * SEGMENT_SECONDS
        segment_transfer = segment["size"] * 8 / (NETWORK_MBPS * 1_000_000)
        segment_display = segment_release + segment_transfer + decode_segment
        rows.append({
            "mode": "segment",
            "request_time_s": start,
            "media_start_s": segment_index * SEGMENT_SECONDS,
            "release_time_s": segment_release,
            "bytes": segment["size"],
            "transfer_s": segment_transfer,
            "decode_first_frame_s": decode_segment,
            "first_frame_display_s": segment_display,
            "end_to_end_s": segment_display - segment_index * SEGMENT_SECONDS,
            "request_to_first_frame_s": segment_display - start,
        })
    for mode in ("part", "segment"):
        selected = [row for row in rows if row["mode"] == mode]
        summary = {
            "mean_end_to_end_s": statistics.fmean(r["end_to_end_s"] for r in selected),
            "mean_request_to_first_frame_s": statistics.fmean(
                r["request_to_first_frame_s"] for r in selected
            ),
            "mean_transfer_s": statistics.fmean(r["transfer_s"] for r in selected),
        }
        for row in selected:
            row["mean_end_to_end_s"] = summary["mean_end_to_end_s"]
            row["mean_request_to_first_frame_s"] = summary[
                "mean_request_to_first_frame_s"
            ]
    return rows


def main():
    source = ROOT / "artifacts/media/vbv.mp4"
    out = ROOT / "artifacts/lhls"
    snapshots = out / "snapshots"
    snapshots.mkdir(parents=True, exist_ok=True)
    init, parts, segments = split_media(source, out)
    count = len(parts)

    hint_texts = []
    for index in range(count):
        text = playlist(count, parts, segments, index)
        (snapshots / f"t{index * PART_SECONDS:06.3f}.m3u8").write_text(text)
        hint_texts.append(text)
    (out / "playlist-live.m3u8").write_text(
        (snapshots / f"t{(count - 1) * PART_SECONDS:06.3f}.m3u8").read_text()
    )

    assembled = out / "assembled.mp4"
    assembled.write_bytes(
        (out / "init.mp4").read_bytes()
        + b"".join(part["path"].read_bytes() for part in parts)
    )
    frames = probe_frames(assembled)
    checks = validate(parts, segments, init, hint_texts, frames)

    part_rows = []
    hint_rows = []
    for part in parts:
        frame = next(f for f in frames if abs(f["pts"] - part["tfdt"]) < 1e-6)
        part_rows.append({
            "part": part["index"],
            "segment": part["index"] // SEGMENT_PARTS,
            "uri": f'part-{part["index"]:03d}.m4s',
            "duration_s": f'{part["duration"]:.3f}',
            "first_frame_s": f'{part["tfdt"]:.3f}',
            "keyframe": "yes" if frame["key"] else "no",
            "idr": "yes" if part["idr"] else "no",
            "bytes": part["size"],
            "hinted": "yes" if part["index"] < count else "no",
            "hinted_first_frame_s": f'{part["index"] * PART_SECONDS:.3f}',
            "hint_start_match": (
                "yes" if abs(part["tfdt"] - part["index"] * PART_SECONDS) < 1e-6 else "no"
            ),
        })
        hint_rows.append({
            "snapshot_s": f'{part["index"] * PART_SECONDS:.3f}',
            "hint_uri": f'part-{part["index"]:03d}.m4s',
            "expected_first_frame_s": f'{part["index"] * PART_SECONDS:.3f}',
            "actual_first_frame_s": f'{part["tfdt"]:.3f}',
            "duration_s": f'{part["duration"]:.3f}',
            "keyframe": "yes" if frame["key"] else "no",
            "idr": "yes" if part["idr"] else "no",
            "match": (
                "yes" if abs(part["tfdt"] - part["index"] * PART_SECONDS) < 1e-6 else "no"
            ),
        })

    ffmpeg = find_tool("ffmpeg")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        part_file = tmp / "part.mp4"
        segment_file = tmp / "segment.mp4"
        part_file.write_bytes(
            (out / "init.mp4").read_bytes() + parts[4]["path"].read_bytes()
        )
        segment_file.write_bytes(
            (out / "init.mp4").read_bytes() + segments[1]["path"].read_bytes()
        )
        decode_part = decode_first_frame_seconds(part_file, ffmpeg)
        decode_segment = decode_first_frame_seconds(segment_file, ffmpeg)

    latency = simulate_latency(parts, segments, decode_part, decode_segment, out)
    summary = {}
    for mode in ("part", "segment"):
        rows = [r for r in latency if r["mode"] == mode]
        summary[mode] = {
            key: statistics.fmean(row[key] for row in rows)
            for key in (
                "end_to_end_s", "request_to_first_frame_s", "transfer_s",
                "decode_first_frame_s",
            )
        }
    summary["part_minus_segment"] = {
        key: summary["part"][key] - summary["segment"][key]
        for key in ("end_to_end_s", "request_to_first_frame_s")
    }

    write_csv(
        ROOT / "artifacts/results/lhls_parts.csv",
        list(part_rows[0].keys()), part_rows,
    )
    write_csv(
        ROOT / "artifacts/results/latency.csv",
        list(latency[0].keys()), latency,
    )
    write_csv(
        ROOT / "artifacts/results/preload_hints.csv",
        list(hint_rows[0].keys()), hint_rows,
    )
    write_json(ROOT / "artifacts/results/lhls_validation.json", {
        "checks": checks,
        "all_checks_pass": all(
            value is True for key, value in checks.items()
            if key not in (
                "part_count", "segment_count", "media_timescale", "duration",
                "hint_count",
            )
        ),
        "part_count": count,
        "segment_count": len(segments),
        "network_mbps": NETWORK_MBPS,
        "start_times_s": START_TIMES,
        "latency_summary": summary,
        "playlist_tag_counts": {
            "EXT-X-PART": sum(text.count("#EXT-X-PART:") for text in hint_texts),
            "EXT-X-PRELOAD-HINT": sum(
                text.count("#EXT-X-PRELOAD-HINT:") for text in hint_texts
            ),
        },
    })
    print(json.dumps({
        "validation": checks,
        "latency_summary": summary,
    }, indent=2))


if __name__ == "__main__":
    main()
