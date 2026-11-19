"""Rank consistency, low-score audit and mismatch list."""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import numpy as np
from scipy.stats import kendalltau, spearmanr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import CONFIG  # noqa: E402

OUT = ROOT / "outputs"


def main() -> None:
    rows = list(csv.DictReader(open(OUT / "segment_scores.csv")))
    for row in rows:
        for key in (
            "fusedScore",
            "referenceScore",
            "subjectiveMos",
            "fusedRank",
            "referenceRank",
            "subjectiveRank",
            "rankDelta",
            "absoluteRankDelta",
        ):
            row[key] = float(row[key])

    fused_rank = [row["fusedRank"] for row in rows]
    ref_rank = [row["referenceRank"] for row in rows]
    subj_rank = [row["subjectiveRank"] for row in rows]
    comparison = {
        "spearmanFusedVsReference": float(spearmanr(fused_rank, ref_rank).statistic),
        "kendallTauFusedVsReference": float(kendalltau(fused_rank, ref_rank).statistic),
        "spearmanFusedVsSubjective": float(
            spearmanr(fused_rank, subj_rank).statistic
        ),
        "spearmanReferenceVsSubjective": float(
            spearmanr(ref_rank, subj_rank).statistic
        ),
    }

    mismatches = [
        {
            "id": row["id"],
            "start": int(float(row["start"])),
            "end": int(float(row["end"])),
            "distortion": row["distortion"],
            "fusedRank": int(row["fusedRank"]),
            "referenceRank": int(row["referenceRank"]),
            "rankDelta": int(row["rankDelta"]),
            "absoluteRankDelta": int(row["absoluteRankDelta"]),
            "reason": _mismatch_reason(row),
        }
        for row in rows
        if row["absoluteRankDelta"] >= CONFIG["significantRankDelta"]
    ]
    mismatches.sort(key=lambda item: -item["absoluteRankDelta"])

    low_fused = sorted(
        (row["id"] for row in rows if row["fusedScore"] < CONFIG["lowFusedScore"])
    )
    low_subjective = sorted(
        (row["id"] for row in rows if row["subjectiveMos"] < CONFIG["lowSubjectiveMos"])
    )
    intersection = sorted(set(low_fused) & set(low_subjective))
    audit = {
        "thresholds": {
            "fusedScoreBelow": CONFIG["lowFusedScore"],
            "subjectiveMosBelow": CONFIG["lowSubjectiveMos"],
        },
        "fusedLow": low_fused,
        "subjectiveLow": low_subjective,
        "intersection": intersection,
        "fusedOnly": sorted(set(low_fused) - set(low_subjective)),
        "subjectiveOnly": sorted(set(low_subjective) - set(low_fused)),
        "precision": _safe_div(len(intersection), len(low_fused)),
        "recall": _safe_div(len(intersection), len(low_subjective)),
        "exactSetMatch": low_fused == low_subjective,
    }
    audit["f1"] = _safe_div(
        2 * audit["precision"] * audit["recall"],
        audit["precision"] + audit["recall"],
    )

    report = {
        "comparison": comparison,
        "significantRankDeltaThreshold": CONFIG["significantRankDelta"],
        "mismatches": mismatches,
        "allRankDifferences": sorted(
            (
                {
                    "id": row["id"],
                    "start": int(float(row["start"])),
                    "end": int(float(row["end"])),
                    "rankDelta": int(row["rankDelta"]),
                    "significant": row["absoluteRankDelta"]
                    >= CONFIG["significantRankDelta"],
                }
                for row in rows
            ),
            key=lambda item: -abs(item["rankDelta"]),
        ),
        "lowScoreAudit": audit,
    }
    (OUT / "rank_comparison.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False)
    )
    _write_mismatch_csv(mismatches, rows)
    print(json.dumps(comparison, indent=2))
    print("mismatches:", [item["id"] for item in mismatches])
    print("low-score exact match:", audit["exactSetMatch"])


def _safe_div(numerator: float, denominator: float) -> float:
    return float(numerator / denominator) if denominator else 0.0


def _write_mismatch_csv(mismatches: list[dict], rows: list[dict]) -> None:
    score_by_id = {row["id"]: row for row in rows}
    fields = [
        "id",
        "start",
        "end",
        "distortion",
        "fusedScore",
        "referenceScore",
        "fusedRank",
        "referenceRank",
        "rankDelta",
        "absoluteRankDelta",
        "reason",
    ]
    with (OUT / "ranking_mismatches.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in mismatches:
            source = score_by_id[item["id"]]
            writer.writerow(
                {
                    **item,
                    "fusedScore": source["fusedScore"],
                    "referenceScore": source["referenceScore"],
                    "reason": item["reason"],
                }
            )


def _mismatch_reason(row: dict) -> str:
    delta = int(row["rankDelta"])
    distortion = row["distortion"]
    if delta < 0:
        if "noise" in distortion:
            return (
                "噪声不破坏边缘与运动，luma SSIM 对颗粒过度惩罚；"
                "融合分因细节/运动保留完好而排名更高"
            )
        return (
            "多指标融合奖励了 luma SSIM 低估的细节/运动保持，融合排名高于参考"
        )
    if "downscale" in distortion or "blur" in distortion:
        return (
            "模糊/降采样摧毁高频细节，SSIM 在平坦区域仍偏高，"
            "细节分量大幅下降导致融合排名更低"
        )
    return (
        "细节或色度保真损伤未被 luma SSIM 充分体现，融合排名低于参考"
    )


if __name__ == "__main__":
    main()
