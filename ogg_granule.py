#!/usr/bin/env python3
"""Command-line entry point for Ogg granule/time mapping analysis."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ogg_timeline.report import (
    build_report_payload,
    render_markdown,
    write_page_csv,
    write_reports,
)


ROOT = Path(__file__).resolve().parent
DEFAULT_FIXTURES = [
    ROOT / "fixtures" / "continuous.vorbis.ogg",
    ROOT / "fixtures" / "discontinuous.vorbis.ogg",
    ROOT / "fixtures" / "continuous.opus.ogg",
    ROOT / "fixtures" / "discontinuous.opus.ogg",
]


def _write_single_outputs(args: argparse.Namespace, payload: dict) -> None:
    markdown = render_markdown(payload)
    wrote = False
    if args.json:
        Path(args.json).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        wrote = True
    if args.markdown:
        Path(args.markdown).write_text(markdown + "\n", encoding="utf-8")
        wrote = True
    if args.csv:
        write_page_csv(payload, args.csv)
        wrote = True
    if not wrote:
        print(markdown)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Parse Ogg page granules and map packets to playback time."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect = subparsers.add_parser(
        "analyze", help="analyze one or more Ogg files"
    )
    inspect.add_argument("files", nargs="+")
    inspect.add_argument("--json", help="write full JSON output")
    inspect.add_argument("--md", dest="markdown", help="write Markdown output")
    inspect.add_argument("--csv", help="write one row per Ogg page")

    demo = subparsers.add_parser(
        "demo", help="analyze checked-in fixtures and write artifacts/"
    )
    demo.add_argument("--out", default=str(ROOT / "artifacts"))

    args = parser.parse_args()
    if args.command == "demo":
        payload = write_reports(DEFAULT_FIXTURES, args.out)
        print(
            f'wrote {args.out}/report.json, report.md, pages.csv '
            f'({len(payload["fixtures"])} streams)'
        )
        return 0

    payload = build_report_payload(args.files)
    _write_single_outputs(args, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
