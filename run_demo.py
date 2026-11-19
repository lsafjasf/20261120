#!/usr/bin/env python3
"""Run the ClearKey/PlayReady rotation demonstration."""

from __future__ import annotations

import argparse
from pathlib import Path

from drm_sim.demo import PROVIDERS, ROOT, run_demo


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "artifacts",
        help="artifact directory (default: artifacts/)",
    )
    parser.add_argument(
        "--print-markdown",
        action="store_true",
        help="print the generated report.md after the summary",
    )
    args = parser.parse_args()
    report = run_demo(args.out)

    print("Checks")
    for name, passed in report["checks"].items():
        print(f"  {'PASS' if passed else 'FAIL'}  {name}")
    for provider in PROVIDERS:
        result = report["providers"][provider]
        diff = result["pssh"]["diff"]
        audit = result["whole_file_byte_audit"]
        print(f"\n{provider}")
        print(f"  state sequence: {' -> '.join(result['state_sequence'])}")
        print(
            "  pssh diff: "
            f"{diff['changed_byte_count']} bytes in "
            f"{len(diff['changed_ranges'])} ranges"
        )
        for change in diff["changed_ranges"]:
            print(
                f"    @{change['offset']:>3} {change['length']:>2}B "
                f"{change['before_hex']} -> {change['after_hex']} "
                f"[{change['field']}]"
            )
        print(
            "  whole-file: "
            f"only_license_fields={audit['only_license_fields_changed']}, "
            f"mdat_unchanged={audit['media_payload']['unchanged']}"
        )
        print(
            "  player: "
            f"parsed={result['player_after_rewrite']['parsed']}, "
            f"kid==default={result['player_after_rewrite']['header_kid_matches_default']}"
        )
    hevc = report["parameter_sets"]
    print("\nHEVC parameter sets")
    print(f"  NAL types: {hevc['nal_types']}")
    print(f"  {hevc['error_code']}: {hevc['message']}")
    print(f"\nArtifacts: {Path(args.out).resolve()}")
    if args.print_markdown:
        print()
        print((Path(args.out) / "report.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
