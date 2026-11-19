from __future__ import annotations

import argparse
from pathlib import Path

from .report import build_report, write_report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate DASH EventStream period boundaries and noise stats"
    )
    parser.add_argument(
        "--mpd",
        default="fixtures/two_period.mpd",
        help="path to the two-Period MPD fixture",
    )
    parser.add_argument(
        "--output-dir",
        default="artifacts",
        help="directory for Markdown, JSON and CSV reports",
    )
    parser.add_argument(
        "--until",
        type=float,
        default=None,
        help="optional media-time end of the simulation",
    )
    args = parser.parse_args()

    report = build_report(args.mpd, until=args.until)
    paths = write_report(args.output_dir, report)
    print(report["markdown"])
    print("Reports:")
    for kind, path in paths.items():
        print(f"  {kind}: {Path(path)}")
    return 0 if all(report["checks"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
