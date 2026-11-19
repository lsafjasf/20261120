# -*- coding: utf-8 -*-
"""Command line entry: run samples or a JSON project, export results."""

from __future__ import annotations

import argparse
import json
import os

from .audio import export_placement_list, render_narration_track
from .models import DescriptionClip, Interval, Timeline
from .samples import SAMPLES
from .scheduler import Scheduler
from .timeline import render


def load_timeline(path: str) -> Timeline:
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    return Timeline(
        duration=float(d["duration"]),
        dialogue=[Interval(*row) for row in d.get("dialogue", [])],
        sfx_zones=[Interval(*row) for row in d.get("sfx_zones", [])],
        clips=[DescriptionClip(**c) for c in d.get("clips", [])],
    )


def run_one(name: str, tl: Timeline, outdir: str) -> None:
    res = Scheduler(tl).schedule()
    print(render(tl, res))
    os.makedirs(outdir, exist_ok=True)
    wav = os.path.join(outdir, f"{name}_narration.wav")
    lst = os.path.join(outdir, f"{name}_placements.json")
    render_narration_track(res, wav, total_duration=tl.duration)
    export_placement_list(res, lst)
    print(f"已导出: {wav}")
    print(f"已导出: {lst}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        prog="ad_scheduler", description="无障碍电影解说轨编排器")
    ap.add_argument("source", nargs="?",
                    help="样例名(%s) 或 JSON 工程文件; 缺省运行全部样例"
                         % "/".join(SAMPLES))
    ap.add_argument("--out", default="output", help="导出目录")
    args = ap.parse_args(argv)

    if args.source is None:
        for name, (title, fn) in SAMPLES.items():
            print("=" * 72)
            print(f"样例 {name}: {title}")
            print("=" * 72)
            run_one(name, fn(), args.out)
            print()
    elif args.source in SAMPLES:
        title, fn = SAMPLES[args.source]
        print(f"样例 {args.source}: {title}")
        run_one(args.source, fn(), args.out)
    else:
        run_one(os.path.splitext(os.path.basename(args.source))[0],
                load_timeline(args.source), args.out)


if __name__ == "__main__":
    main()
