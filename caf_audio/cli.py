"""Command-line interface for CAF inspection and acceptance checks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .caf import CAFFile
from .errors import CafError
from .extended import decode_extended_hex, encode_extended
from .selftest import EXTENDED_REFERENCES, run
from .timebase import (
    FOUR_TIMEBASES,
    boundary_values,
    conversion_boundaries,
    drop_frame_checks,
)


def _json(data: object) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _inspect(args: argparse.Namespace) -> int:
    caf = CAFFile(args.path)
    if args.expect_endian:
        caf.assert_endianness(args.expect_endian)
    analysis = caf.analysis(args.samples)
    if args.json:
        _json(analysis)
        return 0

    desc = analysis["audio_description"]
    layout = analysis["channel_layout"]
    print(f"file: {analysis['file']}")
    print(f"CAF version: {analysis['header']['version']}")
    print(f"chunk order: {' -> '.join(analysis['chunk_order'])}")
    print(
        f"audio format: {desc['format_id']} / {desc['sample_kind']} / "
        f"{desc['sample_rate']:g} Hz / {desc['channels_per_frame']} ch"
    )
    print(
        f"frame count: {analysis['frame_count']} "
        f"(container {analysis['container_frame_count']})"
    )
    print(
        f"big-endian flag: {desc['big_endian']} "
        f"(formatFlags={desc['format_flags']}, bit 1="
        f"{desc['little_endian_flag']})"
    )
    print(
        f"channel layout: {layout['layout_name']} "
        f"[{', '.join(layout['channels'])}]"
    )
    print(
        f"audio data offset: {analysis['audio_data']['payload_offset']} "
        f"(edit count {analysis['audio_data']['edit_count']})"
    )
    for sample in analysis["sample_frames"]:
        offsets = ", ".join(
            f"{item['name']}@{item['file_offset']}"
            for item in sample["offset"]["channels"]
        )
        print(
            f"frame {sample['frame']}: offset={sample['offset']['file_offset']} "
            f"raw={sample['raw_hex']} samples={sample['samples']} "
            f"channels=[{offsets}]"
        )
    for warning in analysis["warnings"]:
        print(f"warning: {warning}", file=sys.stderr)
    return 0


def _timebase(args: argparse.Namespace) -> int:
    data = {
        "timebases": [base.name for base in FOUR_TIMEBASES],
        "boundaries": boundary_values(),
        "drop_frame": drop_frame_checks(),
        "conversions": conversion_boundaries(),
    }
    if args.json:
        _json(data)
        return 0
    for row in data["boundaries"]:
        print(
            f"{row['timebase']:>7} {row['name']:<13} "
            f"frame={row['frame']:<9} tc={row['timecode']} "
            f"seconds={row['seconds']:.6f}"
        )
    drop = data["drop_frame"]
    print()
    print(
        "drop-frame first drop: "
        f"{drop['before_first_drop']['timecode']} -> "
        f"{drop['after_first_drop']['timecode']} "
        f"(frames {drop['before_first_drop']['frame']} -> "
        f"{drop['after_first_drop']['frame']})"
    )
    print(
        f"drop-frame 10-minute boundary: "
        f"{drop['tenth_minute_boundary']['frame']} "
        f"{drop['tenth_minute_boundary']['timecode']}"
    )
    print(
        f"drop-frame hour boundary: {drop['hour_boundary']['frame']} "
        f"{drop['hour_boundary']['timecode']}"
    )
    return 0


def _extended(args: argparse.Namespace) -> int:
    rows = []
    for value, reference in EXTENDED_REFERENCES.items():
        encoded = encode_extended(value).hex()
        decoded = decode_extended_hex(reference)
        rows.append(
            {
                "value": value,
                "reference_hex": reference,
                "encoded_hex": encoded,
                "decoded": decoded,
                "match": encoded == reference and decoded == value,
            }
        )
    if args.json:
        _json({"references": rows})
    else:
        for row in rows:
            print(
                f"{row['value']:>8g} {row['reference_hex']} "
                f"decoded={row['decoded']:g} match={row['match']}"
            )
    return 0 if all(row["match"] for row in rows) else 1


def _selftest(args: argparse.Namespace) -> int:
    report = run(args.out)
    summary = {
        "all_passed": report["all_passed"],
        "checks": report["checks"],
        "output": str(Path(args.out)),
    }
    if args.json:
        _json(summary)
    else:
        print(f"all checks passed: {report['all_passed']}")
        for name, passed in report["checks"].items():
            print(f"  {'PASS' if passed else 'FAIL'} {name}")
        print(f"report: {Path(args.out) / 'report.json'}")
        print(f"report: {Path(args.out) / 'report.md'}")
    return 0 if report["all_passed"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="caf-audio",
        description="Inspect CAF/PCM byte order, SMPTE timecode, and "
        "80-bit extended floats.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect = subparsers.add_parser("inspect", help="inspect a CAF file")
    inspect.add_argument("path")
    inspect.add_argument("--samples", type=int, default=8)
    inspect.add_argument("--json", action="store_true")
    inspect.add_argument("--expect-endian", choices=("big", "little"))
    inspect.set_defaults(handler=_inspect)

    timebase = subparsers.add_parser(
        "timebase", help="show four-timebase boundaries"
    )
    timebase.add_argument("--json", action="store_true")
    timebase.set_defaults(handler=_timebase)

    extended = subparsers.add_parser(
        "extended", help="compare 80-bit extended sample rates"
    )
    extended.add_argument("--json", action="store_true")
    extended.set_defaults(handler=_extended)

    selftest = subparsers.add_parser(
        "selftest", help="generate fixtures and the acceptance report"
    )
    selftest.add_argument("--out", default="artifacts")
    selftest.add_argument("--json", action="store_true")
    selftest.set_defaults(handler=_selftest)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (CafError, OSError, ValueError) as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
