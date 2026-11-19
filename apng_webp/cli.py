from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from .fixtures import build_builtin_fixtures
from .model import APNGError, WebPError
from .pipeline import (
    convert_apng,
    preflight_apng,
    verify_files,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="apng-webp",
        description=(
            "Rebuild APNG frames, convert them to lossless animated WebP, "
            "and verify the decoded result."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser(
        "preflight", help="inspect APNG frames and millisecond timing"
    )
    preflight.add_argument("input", type=Path)
    preflight.add_argument("--json", action="store_true")
    preflight.add_argument("--report", type=Path)

    convert = subparsers.add_parser(
        "convert", help="convert APNG to lossless animated WebP"
    )
    convert.add_argument("input", type=Path)
    convert.add_argument("-o", "--output", type=Path)
    convert.add_argument(
        "--strict-timing",
        action="store_true",
        help="abort if any frame cannot be represented at one-millisecond granularity",
    )
    convert.add_argument("--json", action="store_true")
    convert.add_argument("--report", type=Path)

    verify = subparsers.add_parser(
        "verify", help="compare a WebP against the APNG source"
    )
    verify.add_argument("input", type=Path)
    verify.add_argument("webp", type=Path)
    verify.add_argument("--json", action="store_true")
    verify.add_argument("--report", type=Path)

    fixtures = subparsers.add_parser(
        "fixtures", help="write the built-in APNG test animations"
    )
    fixtures.add_argument("output_dir", type=Path)
    fixtures.add_argument("--json", action="store_true")
    return parser


def _write_report(report: dict[str, Any], path: Path | None) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _print_json(report: dict[str, Any], path: Path | None) -> None:
    _write_report(report, path)
    print(json.dumps(report, ensure_ascii=False, indent=2))


def _print_timing_diagnostics(diagnostics: list[dict[str, Any]]) -> None:
    for item in diagnostics:
        print(
            f"warning: {item['message']}",
            file=sys.stderr,
        )


def _failure_report(stage: str, exc: Exception) -> dict[str, Any]:
    return {
        "ok": False,
        "stage": stage,
        "error": str(exc),
        "first_error": {
            "reason": "input_error",
            "message": str(exc),
        },
    }


def _run_preflight(args: argparse.Namespace) -> int:
    report = preflight_apng(args.input)
    if args.json:
        _print_json(report, args.report)
    else:
        canvas = report["canvas"]
        print(
            f"{args.input}: {canvas['width']}x{canvas['height']}, "
            f"{report['frame_count']} frames, loop={report['loop_count']}, "
            f"{report['total_duration_ms']}ms"
        )
        _print_timing_diagnostics(report["diagnostics"])
        _write_report(report, args.report)
    return 0


def _run_convert(args: argparse.Namespace) -> int:
    output = args.output or args.input.with_suffix(".webp")
    result = convert_apng(
        args.input, output, strict_timing=args.strict_timing
    )
    report = result.as_dict()
    report["stage"] = "verify"
    report["output"] = str(output)
    if args.json:
        _print_json(report, args.report)
    else:
        if result.ok:
            print(
                f"converted {args.input} -> {output}: "
                f"{result.webp_frame_count} frames, "
                f"loop={result.webp_loop_count}, "
                f"{sum(result.webp_durations_ms)}ms"
            )
            print("verified: visible pixels, alpha, timing, and loop boundary")
        else:
            error = result.first_error or {}
            print(
                f"verification failed at frame "
                f"{error.get('frame_number', '?')}: "
                f"{error.get('reason', 'unknown')}",
                file=sys.stderr,
            )
            if error.get("bbox"):
                bbox = error["bbox"]
                print(
                    "difference region: "
                    f"x={bbox['x']} y={bbox['y']} "
                    f"width={bbox['width']} height={bbox['height']}",
                    file=sys.stderr,
                )
            print(error.get("message", ""), file=sys.stderr)
        _print_timing_diagnostics(result.timing.diagnostics)
        _write_report(report, args.report)
    return 0 if result.ok else 1


def _run_verify(args: argparse.Namespace) -> int:
    result = verify_files(args.input, args.webp)
    report = result.as_dict()
    report["stage"] = "verify"
    report["output"] = str(args.webp)
    if args.json:
        _print_json(report, args.report)
    elif result.ok:
        print(
            f"verified {args.webp}: {result.webp_frame_count} frames, "
            f"loop={result.webp_loop_count}, "
            f"{sum(result.webp_durations_ms)}ms"
        )
        _print_timing_diagnostics(result.timing.diagnostics)
        _write_report(report, args.report)
    else:
        error = result.first_error or {}
        print(
            f"verification failed at frame {error.get('frame_number', '?')}: "
            f"{error.get('reason', 'unknown')}",
            file=sys.stderr,
        )
        if error.get("bbox"):
            bbox = error["bbox"]
            print(
                "difference region: "
                f"x={bbox['x']} y={bbox['y']} "
                f"width={bbox['width']} height={bbox['height']}",
                file=sys.stderr,
            )
        print(error.get("message", ""), file=sys.stderr)
        _write_report(report, args.report)
    return 0 if result.ok else 1


def _run_fixtures(args: argparse.Namespace) -> int:
    paths = build_builtin_fixtures(args.output_dir)
    report = {
        "ok": True,
        "output_dir": str(args.output_dir),
        "fixtures": [str(path) for path in paths],
    }
    if args.json:
        _print_json(report, None)
    else:
        for path in paths:
            print(path)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "preflight":
            return _run_preflight(args)
        if args.command == "convert":
            return _run_convert(args)
        if args.command == "verify":
            return _run_verify(args)
        if args.command == "fixtures":
            return _run_fixtures(args)
    except (APNGError, WebPError, OSError) as exc:
        report = _failure_report(args.command, exc)
        if getattr(args, "json", False):
            _print_json(report, getattr(args, "report", None))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2
