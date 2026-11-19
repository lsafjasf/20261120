#!/usr/bin/env python3
"""Validate the artifacts and the reproducibility claims."""

from __future__ import annotations

import json
import sys

from common import FIGURES, PRIMARY_KBPS, RESULTS, ROOT


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    results = json.loads((RESULTS / "results.json").read_text())
    comparisons = json.loads((RESULTS / "comparisons.json").read_text())
    reproducibility = json.loads((RESULTS / "reproducibility.json").read_text())
    partition = json.loads((RESULTS / "partition_impulse.json").read_text())

    cells = results["cells"]
    check(len(cells) == 20, f"expected 20 sweep cells, found {len(cells)}")
    primary = [cell for cell in cells if int(cell["desired_kbps"]) == PRIMARY_KBPS]
    check(len(primary) == 4, "primary operating point must contain four cells")
    check(all(cell["converged"] for cell in primary), "primary rates did not converge")
    check(
        comparisons["same_rate_within_one_percent"],
        "primary pair rate difference exceeds 1%",
    )
    check(
        comparisons["x265_ssim_higher_on_both_clips"],
        "x265 does not lead SSIM on both clips",
    )
    check(
        comparisons["x265_hf_correlation_higher_on_both_clips"],
        "x265 does not lead high-frequency structural correlation on both clips",
    )
    check(
        comparisons["x265_slower_on_both_clips"],
        "x265 is not slower on both clips",
    )

    check(len(reproducibility) == 4, "expected four reproducibility groups")
    for key, group in reproducibility.items():
        check(group["bitstream_identical"], f"{key}: bitstreams differ across runs")
        check(group["decoded_identical"], f"{key}: decoded output differs across runs")
        check(group["ssim_range"] <= 1e-12, f"{key}: SSIM is not deterministic")
        check(group["hf_range"] <= 1e-12, f"{key}: HF metric is not deterministic")

    for name, test in partition["tests"].items():
        check(test["within_tolerance"], f"{name}: impulse output outside tolerance")

    per_frame_lines = (RESULTS / "per_frame.csv").read_text().splitlines()
    check(len(per_frame_lines) == 20 * 90 + 1, "per-frame CSV row count is wrong")
    check((RESULTS / "summary.csv").exists(), "summary CSV is missing")
    check((ROOT / "REPORT.md").exists(), "REPORT.md is missing")
    check(
        (FIGURES / "primary_detail_crops.png").exists(),
        "detail-crop figure is missing",
    )
    for figure in results["figures"]:
        check((ROOT / figure).exists(), f"missing figure: {figure}")

    verification = {
        "ok": True,
        "cells": len(cells),
        "primary_rate_difference_pct": comparisons["max_rate_difference_pct"],
        "reproducibility_groups": len(reproducibility),
        "partition_tests": len(partition["tests"]),
        "per_frame_rows": len(per_frame_lines) - 1,
        "figures": len(results["figures"]),
    }
    (RESULTS / "verification.json").write_text(
        json.dumps(verification, indent=2) + "\n"
    )
    print(json.dumps(verification, indent=2))


if __name__ == "__main__":
    try:
        main()
    except AssertionError as error:
        print(f"verification failed: {error}", file=sys.stderr)
        raise
