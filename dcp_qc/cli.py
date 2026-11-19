from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from . import __version__
from .fixture_builder import (
    build_inherited_missing,
    build_rate_overflow,
    build_supplemental,
    build_two_reel,
)
from .inspector import inspect_package
from .model import InspectionReport
from .render import render_report


def _add_report_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument(
        "--no-hash",
        action="store_true",
        help="check presence only; do not stream asset hashes",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=1024 * 1024,
        metavar="BYTES",
        help="streaming read size (default: 1048576)",
    )
    parser.add_argument("--width", type=int, default=64, help=argparse.SUPPRESS)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dcp-qc",
        description="Offline DCP package QC and reel timeline inspector",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect = subparsers.add_parser(
        "inspect", help="inspect an ASSETMAP/PKL/CPL package"
    )
    inspect.add_argument("package", type=Path)
    inspect.add_argument("--cpl", help="CPL UUID to inspect")
    inspect.add_argument(
        "--base",
        action="append",
        default=[],
        metavar="PACKAGE",
        help="base package used to resolve inherited assets (repeatable)",
    )
    _add_report_options(inspect)

    demo = subparsers.add_parser(
        "demo", help="build and inspect the built-in placeholder cases"
    )
    demo.add_argument(
        "scenario",
        nargs="?",
        default="valid",
        choices=[
            "valid",
            "supplemental",
            "inherited-missing",
            "rate-overflow",
            "all",
        ],
    )
    demo.add_argument(
        "--workdir",
        type=Path,
        help="keep generated packages in this directory",
    )
    _add_report_options(demo)

    build = subparsers.add_parser(
        "build-fixtures", help="write the placeholder DCP packages to a directory"
    )
    build.add_argument("output", type=Path)
    return parser


def _inspect_args(args: argparse.Namespace) -> InspectionReport:
    return inspect_package(
        args.package,
        cpl_id=getattr(args, "cpl", None),
        base_packages=tuple(getattr(args, "base", [])),
        verify_hashes=not args.no_hash,
        chunk_size=args.chunk_size,
    )


def _print_report(report: InspectionReport, args: argparse.Namespace) -> None:
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(render_report(report, width=args.width))


def _run_demo(args: argparse.Namespace) -> int:
    if args.workdir:
        args.workdir.mkdir(parents=True, exist_ok=True)
        temporary = None
        workdir = args.workdir
    else:
        temporary = tempfile.TemporaryDirectory(prefix="dcp-qc-demo-")
        workdir = Path(temporary.name)

    scenarios = (
        ["valid", "supplemental", "inherited-missing", "rate-overflow"]
        if args.scenario == "all"
        else [args.scenario]
    )
    reports: list[tuple[str, InspectionReport]] = []
    try:
        for scenario in scenarios:
            reports.append(
                (scenario, _build_scenario(workdir, scenario, args))
            )
    finally:
        if temporary is not None:
            temporary.cleanup()

    if args.json:
        payload = [
            {"scenario": name, "report": report.to_dict()}
            for name, report in reports
        ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for index, (name, report) in enumerate(reports):
            if index:
                print("\n" + "=" * 78 + "\n")
            print(f"Scenario: {name}")
            print(render_report(report, width=args.width))
    return 0 if all(report.ok for _, report in reports) else 1


def _build_scenario(
    workdir: Path, scenario: str, args: argparse.Namespace | None = None
) -> InspectionReport:
    verify_hashes = not args.no_hash if args else True
    chunk_size = args.chunk_size if args else 1024 * 1024
    if scenario == "valid":
        package = build_two_reel(workdir / "base_two_reel")
        return inspect_package(
            package, verify_hashes=verify_hashes, chunk_size=chunk_size
        )
    if scenario == "supplemental":
        base = build_two_reel(workdir / "base_for_supplemental")
        supplement = build_supplemental(workdir / "supplemental")
        return inspect_package(
            supplement,
            base_packages=(base,),
            verify_hashes=verify_hashes,
            chunk_size=chunk_size,
        )
    if scenario == "inherited-missing":
        supplement, base = build_inherited_missing(
            workdir / "inherited_missing"
        )
        return inspect_package(
            supplement,
            base_packages=(base,),
            verify_hashes=verify_hashes,
            chunk_size=chunk_size,
        )
    if scenario == "rate-overflow":
        package = build_rate_overflow(workdir / "rate_overflow")
        return inspect_package(
            package, verify_hashes=verify_hashes, chunk_size=chunk_size
        )
    raise ValueError(f"unknown scenario: {scenario}")


def _build_fixtures(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    build_two_reel(output / "base_two_reel")
    build_supplemental(output / "supplemental")
    build_rate_overflow(output / "rate_overflow")
    supplement, base = build_inherited_missing(
        output / "faults" / "inherited_missing"
    )
    print(f"Wrote base_two_reel, supplemental, rate_overflow to {output}")
    print(f"Inherited-missing case: {supplement} --base {base}")


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if getattr(args, "chunk_size", 1) <= 0:
        parser.error("--chunk-size must be positive")
    if args.command == "inspect":
        try:
            report = _inspect_args(args)
        except (FileNotFoundError, ValueError, OSError) as exc:
            print(f"dcp-qc: {exc}", file=sys.stderr)
            return 2
        _print_report(report, args)
        return 0 if report.ok else 1
    if args.command == "demo":
        return _run_demo(args)
    if args.command == "build-fixtures":
        _build_fixtures(args.output)
        return 0
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
