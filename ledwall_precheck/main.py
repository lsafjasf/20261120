"""CLI entry point: synthesize test clips, analyze takes, build report.

Usage:
    python -m ledwall_precheck.main synth   --data data
    python -m ledwall_precheck.main analyze --data data --out output
    python -m ledwall_precheck.main demo    --data data --out output
"""

from __future__ import annotations

import argparse
import json
import os

from .analyze import analyze_take
from .report import write_report
from .synth import synthesize_dataset


def _cmd_synth(args) -> None:
    takes = synthesize_dataset(args.data, n_frames=args.frames)
    print(f"synthesized {len(takes)} takes -> {args.data}")


def _cmd_analyze(args) -> None:
    log_path = os.path.join(args.data, "shoot_log.json")
    with open(log_path) as fh:
        takes = json.load(fh)
    results = []
    for entry in takes:
        take_dir = os.path.join(args.data, entry["take"])
        res = analyze_take(take_dir)
        results.append(res)
        print(f"{entry['take']:>20}: {res['verdict']:<18} risk={res['risk']}"
              + (" (ambiguous)" if res.get("ambiguous") else ""))
    report = write_report(results, args.out)
    print(f"report -> {report}")


def main() -> None:
    ap = argparse.ArgumentParser(description="LED wall shutter pre-check")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("synth", "analyze", "demo"):
        p = sub.add_parser(name)
        p.add_argument("--data", default="data")
        p.add_argument("--out", default="output")
        p.add_argument("--frames", type=int, default=48)
    args = ap.parse_args()
    if args.cmd in ("synth", "demo"):
        _cmd_synth(args)
    if args.cmd in ("analyze", "demo"):
        _cmd_analyze(args)


if __name__ == "__main__":
    main()
