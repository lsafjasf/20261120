"""Command line interface for the offline shutter integrator."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from .imageio import load_frame
from .integrator import integrate_frames
from .manifest import load_manifest
from .pipeline import integrate_manifest
from .preview import center_instant, contact_sheet
from .validate import run_validation
from .weights import TRIANGLE, UNIFORM, get_weight
from .demo import generate_sample_data


def _cmd_integrate(args) -> int:
    manifest = load_manifest(args.manifest)
    weight = get_weight(args.weight)
    records = integrate_manifest(manifest, weight, args.out)
    flagged = [r["index"] for r in records if r["status"] != "ok"]
    print(f"integrated {len(records)} frame(s) with {weight.name} shutter")
    if flagged:
        print(f"WARNING: frames affected by missing subframes: {flagged}")
    return 0


def _cmd_preview(args) -> int:
    manifest = load_manifest(args.manifest)
    rows, labels, flagged, flag_texts = [], [], [], []
    for f in manifest.frames:
        frames = np.stack(
            [load_frame(manifest.resolve(s)) for s in f.subframes]
        )
        cols = [center_instant(frames, f.times, f.t_open, f.t_close)]
        for weight in (UNIFORM, TRIANGLE):
            cols.append(
                integrate_frames(
                    frames, f.times, f.t_open, f.t_close, weight
                ).frame
            )
        rows.append(cols)
        miss = f.missing_times()
        tag = f"f{f.index:04d}"
        labels.append(
            [f"{tag} center", f"{tag} uniform", f"{tag} triangle"]
        )
        flagged.append([bool(miss)] * 3)
        flag_texts.append([f"MISSING {len(miss)}"] * 3)
    sheet = contact_sheet(rows, labels, flagged, flag_texts)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    sheet.save(args.out)
    print(f"preview written to {args.out}")
    return 0


def _cmd_validate(args) -> int:
    report = run_validation(args.out, args.frames, args.subframes)
    for c in report["checks"]:
        mark = "PASS" if c["passed"] else "FAIL"
        print(f"[{mark}] {c['name']}")
    print(f"report: {Path(args.out) / 'report.json'}")
    return 0 if report["all_passed"] else 1


def _cmd_demo(args) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    manifests = generate_sample_data(out, args.frames, args.subframes)
    print(f"sample data + manifests written to {out}")
    rc = 0
    for name, mpath in manifests.items():
        for weight in ("uniform", "triangle"):
            rc |= main(
                [
                    "integrate",
                    "--manifest", str(mpath),
                    "--out", str(out / "integrated" / name),
                    "--weight", weight,
                ]
            )
        rc |= main(
            [
                "preview",
                "--manifest", str(mpath),
                "--out", str(out / f"preview_{name}.png"),
            ]
        )
    rc |= main(
        [
            "validate",
            "--out", str(out / "validation"),
            "--frames", str(args.frames),
            "--subframes", str(args.subframes),
        ]
    )
    return rc


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="shutter-integrator",
        description="Offline shutter integrator for 2D animation frames",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("integrate", help="integrate a manifest")
    pi.add_argument("--manifest", required=True)
    pi.add_argument("--out", required=True)
    pi.add_argument("--weight", default="uniform",
                    choices=["uniform", "triangle"])
    pi.set_defaults(fn=_cmd_integrate)

    pp = sub.add_parser("preview", help="side-by-side shutter preview")
    pp.add_argument("--manifest", required=True)
    pp.add_argument("--out", required=True)
    pp.set_defaults(fn=_cmd_preview)

    pv = sub.add_parser("validate", help="run the validation suite")
    pv.add_argument("--out", required=True)
    pv.add_argument("--frames", type=int, default=4)
    pv.add_argument("--subframes", type=int, default=9)
    pv.set_defaults(fn=_cmd_validate)

    pd = sub.add_parser("demo", help="replayable end-to-end sample")
    pd.add_argument("--out", required=True)
    pd.add_argument("--frames", type=int, default=4)
    pd.add_argument("--subframes", type=int, default=9)
    pd.set_defaults(fn=_cmd_demo)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
