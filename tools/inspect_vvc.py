"""Command-line VVC Annex B and parameter-set inspector."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vvc_analysis import analyze_stream


def print_summary(report: dict) -> None:
    print(f"stream_size={report['stream_size']} nal_count={report['nal_count']}")
    print("\nParameter-set first occurrences:")
    for item in report["parameter_sets"]:
        values = item["values"]
        details = ""
        if item["kind"] == "SPS":
            details = (
                f" {values['resolution']} {values['bit_depth']}-bit "
                f"{values['chroma_format']}"
            )
        elif item["kind"] == "VPS":
            details = (
                f" layers={values['vps_max_layers_minus1'] + 1}"
                f" sublayers={values['vps_max_sublayers_minus1'] + 1}"
            )
        print(
            f"  {item['kind']}:{item['id']} rev={item['revision']} "
            f"start={item['first_start_offset']} "
            f"code={item['first_start_code_hex']} "
            f"prefix={item['first_prefix_length']} nalu={item['first_nal_offset']}"
            f"{details}"
        )
    if report["sps_events"]:
        print("\nSPS events:")
        for event in report["sps_events"]:
            before = event["before"]
            after = event["after"]
            before_text = (
                f"{before['resolution']} {before['bit_depth']}-bit"
                if before
                else "(start)"
            )
            after_text = f"{after['resolution']} {after['bit_depth']}-bit"
            print(
                f"  {event['event']} at {event['start_offset']}: "
                f"{before_text} -> {after_text}"
            )
    print("\nNAL distribution:")
    for item in report["nal_type_distribution"]:
        print(
            f"  type={item['nal_type']:02d} {item['type_name']} "
            f"count={item['count']} bytes={item['total_bytes']}"
        )
    print("\nUnknown NAL distribution:")
    if not report["unknown_nal_distribution"]:
        print("  none")
    for item in report["unknown_nal_distribution"]:
        print(
            f"  type={item['nal_type']:02d} {item['type_name']} "
            f"count={item['count']} bytes={item['total_bytes']} "
            f"first={item['first_start_offset']}"
        )
    if report["parse_errors"]:
        print("\nParse errors:")
        for item in report["parse_errors"]:
            print(
                f"  NAL {item['nal_index']} at {item['nal_offset']}: "
                f"{item['error']}"
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--unknown-dir", type=Path)
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()
    report = analyze_stream(
        args.input.read_bytes(),
        unknown_dir=args.unknown_dir,
    )
    print_summary(report)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    elif not args.summary_only:
        print("\nJSON:")
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
