"""Command-line interface for MPEG-1 Audio Layer II inspection."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from .parser import Frame, ScanResult, scan


def _bitrate_text(frame: Frame) -> str:
    if frame.header.free_format:
        inferred = frame.inferred_bitrate_kbps
        return f"free({inferred:.3f})" if inferred is not None else "free"
    return str(frame.header.bitrate_kbps)


def _crc_text(frame: Frame) -> str:
    if frame.header.has_crc is False:
        return "-"
    if frame.crc_ok is True:
        return "ok"
    if frame.crc_ok is False:
        return "ERROR"
    return "unknown"


def _print_text(path: str, result: ScanResult, verbose: bool) -> None:
    print(f"{path}:")
    print(
        " idx  offset  bytes  kbps          Hz  pad crc    mode          ch "
        "sidebits  boundary"
    )
    for frame in result.frames:
        header = frame.header
        side_bits = frame.side_info.protected_bits if frame.side_info else "-"
        boundary = "ok" if frame.boundary_ok else "CONFLICT"
        print(
            f"{frame.index:4d} {frame.offset:7d} {frame.length:6d} "
            f"{_bitrate_text(frame):>8s} {header.sample_rate:8d} "
            f"{header.padding:3d} {_crc_text(frame):6s} "
            f"{header.mode_name:12s} {header.channels:2d} "
            f"{str(side_bits):>8s}  {boundary}"
        )
        if verbose and frame.side_info:
            side = frame.side_info
            print(
                f"     side-info: table={side.allocation_table} "
                f"sblimit={side.sblimit} bound={side.bound} "
                f"alloc={side.allocation_bits}b scfsi={side.scfsi_bits}b "
                f"scalefactors={side.scale_factor_bits}b "
                f"crc_protected={side.protected_bits}b"
            )
            for channel in range(side.channels):
                allocation = ",".join(str(value) for value in side.allocation[channel])
                scfsi = ",".join(
                    "-" if value is None else str(value)
                    for value in side.scfsi[channel]
                )
                print(f"     ch{channel} allocation=[{allocation}] scfsi=[{scfsi}]")
        if frame.side_info_error:
            print(f"     side-info error: {frame.side_info_error}")
        if not frame.boundary_ok:
            print(f"     boundary: {frame.boundary_detail}")

    summary = result.summary()
    print(f"total_frames={summary['total_frames']}")
    print(f"audio_seconds={summary['audio_seconds']:.6f}")
    print(f"crc_error_frames={summary['crc_error_frames']}")
    print(f"free_format_frames={summary['free_format_frames']}")
    print(f"boundary_conflict_frames={summary['boundary_conflict_frames']}")
    if result.skipped:
        print(
            "skipped_regions="
            + repr([(item.offset, item.length) for item in result.skipped])
        )
    print()


def _print_csv(results: list[tuple[str, ScanResult]]) -> None:
    writer = csv.writer(sys.stdout)
    writer.writerow(
        [
            "file",
            "frame",
            "offset",
            "length",
            "bitrate_kbps",
            "free_format",
            "inferred_bitrate_kbps",
            "sample_rate",
            "padding",
            "mode",
            "channels",
            "has_crc",
            "crc_ok",
            "boundary_ok",
        ]
    )
    for path, result in results:
        for frame in result.frames:
            writer.writerow(
                [
                    path,
                    frame.index,
                    frame.offset,
                    frame.length,
                    frame.header.bitrate_kbps,
                    int(frame.free_format),
                    frame.inferred_bitrate_kbps,
                    frame.header.sample_rate,
                    frame.header.padding,
                    frame.header.mode_name,
                    frame.header.channels,
                    int(frame.header.has_crc),
                    frame.crc_ok,
                    int(frame.boundary_ok),
                ]
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect MPEG-1 Audio Layer II headers, side info and CRC."
    )
    parser.add_argument("files", nargs="+", help="MP2 or raw MPEG-1 Layer II files")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--csv", action="store_true", help="emit one CSV row per frame")
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="show allocation and SCFSI vectors for every frame",
    )
    parser.add_argument(
        "--fail-on-crc", action="store_true", help="exit 2 if any frame CRC fails"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    results: list[tuple[str, ScanResult]] = []
    for filename in args.files:
        data = Path(filename).read_bytes()
        results.append((filename, scan(data)))

    if args.json:
        payload = (
            results[0][1].to_dict()
            if len(results) == 1
            else {
                path: result.to_dict()
                for path, result in results
            }
        )
        json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    elif args.csv:
        _print_csv(results)
    else:
        for path, result in results:
            _print_text(path, result, args.verbose)

    if args.fail_on_crc and any(result.crc_error_frames for _, result in results):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
