"""Command line entry: python -m vtt_layout <sample.vtt> <player.html> <out>."""

from __future__ import annotations

import sys

from .report import build
from .markdown import render


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__)
        return 2
    report = build(argv[1], argv[2], argv[3])
    render(f"{argv[3]}/layout_report.json", f"{argv[3]}/layout_report.md")
    print(f"cues={report['cue_count']} "
          f"fps_declared={report['frame_rate']['declared_fps']} "
          f"fps_derived={report['frame_rate']['derived_fps']} "
          f"consistent={report['frame_rate']['consistent']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
