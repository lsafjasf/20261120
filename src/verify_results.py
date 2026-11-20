#!/usr/bin/env python3
import csv
import json
from pathlib import Path

from media_utils import ROOT, write_json

RESULTS = ROOT / "artifacts/results"


def load_json(name):
    return json.loads((RESULTS / name).read_text())


def load_csv(name):
    with (RESULTS / name).open() as fh:
        return list(csv.DictReader(fh))


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    validation = load_json("lhls_validation.json")
    early = load_json("early_request_http.json")
    quality = load_json("quality_summary.json")
    parts = load_csv("preload_hints.csv")
    latency = load_csv("latency.csv")

    require(validation["all_checks_pass"], "LL-HLS structural checks failed")
    require(early["all_checks_pass"], "early-request HTTP checks failed")
    require(quality["all_checks_pass"], "quality checks failed")
    require(len(parts) == 24, "expected 24 preload hints")
    require(all(row["match"] == "yes" for row in parts), "hint/first-frame mismatch")
    require(all(row["duration_s"] == "0.500" for row in parts), "bad part duration")
    require(all(row["keyframe"] == "yes" and row["idr"] == "yes" for row in parts),
            "part does not start with keyframe/IDR")

    summary = validation["latency_summary"]
    require(
        summary["part"]["end_to_end_s"] < summary["segment"]["end_to_end_s"],
        "part end-to-end latency is not lower",
    )
    require(
        summary["part"]["request_to_first_frame_s"]
        < summary["segment"]["request_to_first_frame_s"],
        "part startup latency is not lower",
    )
    require(len(latency) == 10, "expected five paired latency samples")
    require(
        quality["ranking"]["by_ssim"] == ["vbv", "cbr", "abr"],
        "unexpected SSIM quality ranking",
    )
    require(
        quality["vbv_static_gap_to_best"] <= 0.001,
        "VBV static quality gap exceeds 0.001 SSIM",
    )

    result = {
        "all_checks_pass": True,
        "checks": {
            "lhls_structure": True,
            "preload_hint_first_frame": True,
            "keyframe_gop_alignment": True,
            "early_request_http": True,
            "part_latency_lower": True,
            "quality_ranking": quality["ranking"]["by_ssim"],
            "vbv_static_gap": quality["vbv_static_gap_to_best"],
        },
    }
    write_json(RESULTS / "verification.json", result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
