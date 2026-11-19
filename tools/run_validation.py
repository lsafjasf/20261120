"""Run the VVC switch, unknown-NAL, and band-transition validation suite."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vvc_analysis import analyze_stream
from vvc_analysis.audio import analyze_band_switch
from vvc_analysis.params import write_json


ROOT = Path(__file__).resolve().parents[1]
STREAMS = ROOT / "samples" / "streams"
DECODED = ROOT / "samples" / "decoded"
REPORT = ROOT / "samples" / "report"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sps_values(report: dict) -> dict:
    for item in report["parameter_sets"]:
        if item["kind"] == "SPS" and not item["error"]:
            return item["values"]
    raise ValueError("stream has no usable SPS")


def frame_size(sps: dict) -> int:
    width = sps["sps_pic_width_max_in_luma_samples"]
    height = sps["sps_pic_height_max_in_luma_samples"]
    chroma = sps["sps_chroma_format_idc"]
    sub_width = 1 if chroma in (0, 3) else 2
    sub_height = 2 if chroma == 1 else 1
    bytes_per_sample = 2 if sps["bit_depth"] > 8 else 1
    luma = width * height
    chroma_planes = 0 if chroma == 0 else 2 * (width // sub_width) * (height // sub_height)
    return (luma + chroma_planes) * bytes_per_sample


def decode(vvdec: Path, bitstream: Path, output: Path) -> dict:
    output.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [str(vvdec), "-b", str(bitstream), "-o", str(output), "-v", "4"],
        text=True,
        capture_output=True,
        check=False,
    )
    (REPORT / f"{output.stem}.decode.log").write_text(
        completed.stdout + completed.stderr, encoding="utf-8"
    )
    log = completed.stdout + completed.stderr
    size_info = re.findall(r"SizeInfo:\s+([0-9]+x[0-9]+)\s+\(([0-9]+)b\)", log)
    frame_match = re.search(r"([0-9]+) frames decoded", log)
    return {
        "returncode": completed.returncode,
        "decoded_frames_logged": int(frame_match.group(1)) if frame_match else None,
        "size_info_sequence": [
            {"resolution": resolution, "bit_depth": int(depth)}
            for resolution, depth in size_info
        ],
        "has_decode_error": "decoding failed" in log.lower(),
        "log": str(REPORT / f"{output.stem}.decode.log"),
    }


def compare_parts(output: Path, parts: list[tuple[str, Path]]) -> list[dict]:
    data = output.read_bytes()
    position = 0
    comparisons = []
    for name, reference in parts:
        ref_data = reference.read_bytes()
        actual = data[position : position + len(ref_data)]
        comparisons.append(
            {
                "part": name,
                "decoded_byte_offset": position,
                "byte_length": len(ref_data),
                "sha256": hashlib.sha256(actual).hexdigest(),
                "reference_sha256": hashlib.sha256(ref_data).hexdigest(),
                "byte_exact_match": actual == ref_data,
            }
        )
        position += len(ref_data)
    return comparisons + [
        {
            "part": "trailing_bytes",
            "decoded_byte_offset": position,
            "byte_length": len(data) - position,
            "byte_exact_match": position == len(data),
        }
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vvdec", type=Path)
    args = parser.parse_args()
    REPORT.mkdir(parents=True, exist_ok=True)
    DECODED.mkdir(parents=True, exist_ok=True)

    low_path = STREAMS / "low_96x64_8bit.vvc"
    high_path = STREAMS / "high_192x128_10bit.vvc"
    switch_path = STREAMS / "switch_low_high_low.vvc"
    unknown_probe = STREAMS / "switch_with_unknown.vvc"
    vps_sample = STREAMS / "vps_sps_pps_multires.vvc"

    low = analyze_stream(low_path.read_bytes())
    high = analyze_stream(high_path.read_bytes())
    switch = analyze_stream(switch_path.read_bytes())
    vps_report = analyze_stream(vps_sample.read_bytes())

    low_sps = sps_values(low)
    high_sps = sps_values(high)
    low_frames = len((DECODED / "low_96x64_8bit_ref.yuv").read_bytes()) // frame_size(low_sps)
    high_frames = len((DECODED / "high_192x128_10bit_ref.yuv").read_bytes()) // frame_size(high_sps)

    decoder_run = None
    if args.vvdec:
        decoder_run = decode(args.vvdec, switch_path, DECODED / "switch_low_high_low.yuv")

    parts = [
        ("low_1", DECODED / "low_96x64_8bit_ref.yuv"),
        ("high_1", DECODED / "high_192x128_10bit_ref.yuv"),
        ("low_2", DECODED / "low_96x64_8bit_ref.yuv"),
    ]
    comparisons = compare_parts(DECODED / "switch_low_high_low.yuv", parts)
    part_frames = {
        "low_1": low_frames,
        "high_1": high_frames,
        "low_2": low_frames,
        "trailing_bytes": 0,
    }
    for item in comparisons:
        item["frame_count"] = part_frames[item["part"]]
    expected_frames = low_frames + high_frames + low_frames
    actual_bytes = (DECODED / "switch_low_high_low.yuv").stat().st_size
    expected_bytes = sum((DECODED / ref).stat().st_size for _, ref in parts)

    missing_unknown_dir = ROOT / "samples" / "unknown_raw"
    unknown_report = analyze_stream(
        unknown_probe.read_bytes(), unknown_dir=missing_unknown_dir
    )
    preservation = [
        {
            "nal_type": item["nal_type"],
            "start_offset": item["start_offset"],
            "byte_exact_match": Path(item["file"]).read_bytes()
            == bytes.fromhex(item["raw_hex"]),
        }
        for item in unknown_report["unknown_nals"]
    ]

    band = analyze_band_switch(
        ROOT / "samples" / "audio" / "band_switch_smooth.wav",
        0.45,
        120.0,
        ROOT / "samples" / "audio" / "band_switch_hard_control.wav",
    )
    band.pop("samples")
    band.pop("frames")

    switch_events = []
    frame_boundaries = [low_frames, low_frames + high_frames]
    byte_boundaries = [
        (DECODED / "low_96x64_8bit_ref.yuv").stat().st_size,
        (DECODED / "low_96x64_8bit_ref.yuv").stat().st_size
        + (DECODED / "high_192x128_10bit_ref.yuv").stat().st_size,
    ]
    for event_index, event in enumerate(switch["sps_events"][1:]):
        switch_events.append(
            {
                "stream_offset": event["start_offset"],
                "before": event["before"],
                "after": event["after"],
                "decoded_byte_boundary": byte_boundaries[event_index],
                "next_frame_index": frame_boundaries[event_index],
            }
        )

    result = {
        "vps_sps_pps_sample": {
            "file": str(vps_sample),
            "provenance": "JVET VVC FDIS conformance bitstream VPS_A_INTEL_3",
            "parameter_set_first_occurrences": [
                {
                    "kind": item["kind"],
                    "id": item["id"],
                    "revision": item["revision"],
                    "start_offset": item["first_start_offset"],
                    "nal_offset": item["first_nal_offset"],
                    "start_code_hex": item["first_start_code_hex"],
                    "prefix_length": item["first_prefix_length"],
                    "occurrences": item["occurrences"],
                    "resolution": item["values"].get("resolution"),
                    "bit_depth": item["values"].get("bit_depth"),
                    "chroma_format": item["values"].get("chroma_format"),
                }
                for item in vps_report["parameter_sets"]
            ],
        },
        "switch_stream": {
            "file": str(switch_path),
            "stream_size": switch["stream_size"],
            "composition": ["96x64/8-bit", "192x128/10-bit", "96x64/8-bit"],
            "sps_events": switch_events,
            "parameter_set_first_occurrences": [
                {
                    "kind": item["kind"],
                    "id": item["id"],
                    "revision": item["revision"],
                    "start_offset": item["first_start_offset"],
                    "nal_offset": item["first_nal_offset"],
                    "start_code_hex": item["first_start_code_hex"],
                    "prefix_length": item["first_prefix_length"],
                    "occurrences": item["occurrences"],
                    "resolution": item["values"].get("resolution"),
                    "bit_depth": item["values"].get("bit_depth"),
                    "chroma_format": item["values"].get("chroma_format"),
                }
                for item in switch["parameter_sets"]
            ],
            "decoder": decoder_run,
            "decoded_bytes": actual_bytes,
            "expected_bytes": expected_bytes,
            "expected_frames": expected_frames,
            "actual_frames": expected_frames if actual_bytes == expected_bytes else None,
            "segment_comparisons": comparisons,
            "no_frame_loss": (
                actual_bytes == expected_bytes
                and all(item["byte_exact_match"] for item in comparisons)
                and (
                    decoder_run is None
                    or decoder_run["decoded_frames_logged"] == expected_frames
                )
            ),
        },
        "unknown_nals": {
            "file": str(unknown_probe),
            "distribution": unknown_report["unknown_nal_distribution"],
            "preservation": preservation,
            "all_preserved": all(item["byte_exact_match"] for item in preservation),
        },
        "band_switch": band,
    }
    write_json(result, REPORT / "validation.json")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
