from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .analysis import AnalysisOptions, analyze_video
from .export import ExportError, export_corrected, validate_corrections
from .fixtures import TestPattern, write_test_video
from .media import MediaError, probe_video
from .preview import render_preview


def _progress(done: int, total: int | None) -> None:
    if done == 1 or done % 24 == 0 or (total and done == total):
        suffix = f"/{total}" if total else ""
        print(f"\rAnalyzing frames {done}{suffix}", end="", file=sys.stderr, flush=True)


def _options(args: argparse.Namespace) -> AnalysisOptions:
    return AnalysisOptions(
        packing=args.packing,
        max_disparity_px=args.max_disparity_px,
        max_disparity_ratio=args.max_disparity_ratio,
        max_vertical_px=args.max_vertical_px,
        assumed_order=args.assume_order,
    )


def _print_report(report: dict[str, Any], verbose: bool) -> None:
    summary = report["summary"]
    identity = report["eye_identity"]
    media = report["media"]
    print(
        f"{summary['status'].upper()}: {summary['frames_analyzed']} frames, "
        f"{media['width']}x{media['height']} {media['packing'].upper()}, "
        f"{media['frame_rate']} fps"
    )
    order = identity.get("selected_order") or "UNCONFIRMED"
    print(
        f"Eye order: {order} ({identity['source']}), "
        f"labels {identity['labeled_frames']}/{summary['frames_analyzed']}"
    )
    print(
        f"Anomalies: {summary['critical_count']} critical, "
        f"{summary['warning_count']} warning"
    )
    for item in report["anomalies"]:
        span = f"frame {item['start_frame']}"
        if item["end_frame"] != item["start_frame"]:
            span += f"-{item['end_frame']}"
        value = f", peak {item['max_value']}px" if "max_value" in item else ""
        print(f"  [{item['severity'].upper()}] {span} {item['eye']}: {item['message']}{value}")
    if verbose:
        for frame in report["frames"]:
            print(
                f"  #{frame['index']:06d} A={frame['first']['source_frame']} "
                f"B={frame['second']['source_frame']} "
                f"FG=({frame['foreground_dx']},{frame['foreground_dy']}) "
                f"BG=({frame['background_dx']},{frame['background_dy']}) "
                f"{','.join(frame['flags']) or 'ok'}"
            )


def _write_report(report: dict[str, Any], path: str | None) -> None:
    if not path:
        return
    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Report: {destination}")


def command_analyze(args: argparse.Namespace) -> int:
    report = analyze_video(args.video, _options(args), progress=_progress)
    print(file=sys.stderr)
    _print_report(report, args.verbose)
    _write_report(report, args.json_out)
    status = report["summary"]["status"]
    return 2 if status == "fail" else (1 if status == "warning" else 0)


def command_preview(args: argparse.Namespace) -> int:
    report = analyze_video(args.video, _options(args), progress=_progress)
    print(file=sys.stderr)
    info = probe_video(args.video)
    order = report["eye_identity"].get("selected_order") or args.order
    image, metadata = render_preview(
        report,
        info,
        args.frame,
        mode=args.mode,
        order=order,
        offset_left=args.offset_left,
        offset_right=args.offset_right,
        show_marks=not args.no_marks,
    )
    destination = Path(args.output).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(image)
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    print(f"Preview: {destination}")
    return 0


def command_export(args: argparse.Namespace) -> int:
    report = analyze_video(args.video, _options(args), progress=_progress)
    print(file=sys.stderr)
    validation = validate_corrections(
        report,
        order=args.order,
        offset_left=args.offset_left,
        offset_right=args.offset_right,
    )
    print(json.dumps(validation, ensure_ascii=False, indent=2))
    result = export_corrected(
        report,
        args.output,
        order=args.order,
        offset_left=args.offset_left,
        offset_right=args.offset_right,
        packaging=args.out_packing,
        force=args.force,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0


def command_serve(args: argparse.Namespace) -> int:
    from .server import serve

    serve(args.host, args.port, open_browser=args.open)
    return 0


def command_make_test(args: argparse.Namespace) -> int:
    output = Path(args.output_dir)
    cases = {
        "normal": TestPattern(packing=args.packing, frames=args.frames),
        "swapped_eyes": TestPattern(
            packing=args.packing,
            frames=args.frames,
            swap_eyes=True,
        ),
        "dropped_frame": TestPattern(
            packing=args.packing,
            frames=args.frames,
            drop_second_at=args.frames // 3,
        ),
    }
    for name, pattern in cases.items():
        path = output / f"{name}_{args.packing}.mp4"
        write_test_video(path, pattern)
        print(path)
    return 0


def _analysis_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("video", help="Packed SBS or OU video")
    parser.add_argument("--packing", choices=("sbs", "ou"), required=True)
    parser.add_argument("--max-disparity-px", type=int)
    parser.add_argument("--max-disparity-ratio", type=float, default=0.025)
    parser.add_argument("--max-vertical-px", type=float, default=2.0)
    parser.add_argument("--assume-order", choices=("ab", "ba"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stereo-preflight",
        description="Preflight packed stereo 3D media for eye order, timing, and parallax errors.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze = subparsers.add_parser("analyze", help="Analyze a packed stereo video")
    _analysis_arguments(analyze)
    analyze.add_argument("--json-out")
    analyze.add_argument("--verbose", action="store_true")
    analyze.set_defaults(handler=command_analyze)

    preview = subparsers.add_parser("preview", help="Render one preview frame as PNG")
    _analysis_arguments(preview)
    preview.add_argument("--frame", type=int, default=0)
    preview.add_argument("--mode", choices=("left", "right", "anaglyph", "marked"), default="anaglyph")
    preview.add_argument("--order", choices=("ab", "ba"), default="ab")
    preview.add_argument("--offset-left", type=int, default=0)
    preview.add_argument("--offset-right", type=int, default=0)
    preview.add_argument("--no-marks", action="store_true")
    preview.add_argument("--output", required=True)
    preview.set_defaults(handler=command_preview)

    export = subparsers.add_parser("export", help="Export a corrected packed video")
    _analysis_arguments(export)
    export.add_argument("output")
    export.add_argument("--order", choices=("auto", "ab", "ba"), default="auto")
    export.add_argument("--offset-left", type=int, default=0)
    export.add_argument("--offset-right", type=int, default=0)
    export.add_argument("--out-packing", choices=("sbs", "ou"))
    export.add_argument("--force", action="store_true")
    export.set_defaults(handler=command_export)

    serve_parser = subparsers.add_parser("serve", help="Start the local review UI")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8765)
    serve_parser.add_argument("--open", action="store_true")
    serve_parser.set_defaults(handler=command_serve)

    make_test = subparsers.add_parser("make-test", help="Generate the three validation clips")
    make_test.add_argument("output_dir", nargs="?", default="artifacts/fixtures")
    make_test.add_argument("--frames", type=int, default=48)
    make_test.add_argument("--packing", choices=("sbs", "ou"), default="sbs")
    make_test.set_defaults(handler=command_make_test)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (MediaError, ExportError, ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
