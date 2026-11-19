"""Run the fused scoring pipeline over every candidate/reference clip pair."""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import fusion, pipeline, video_io  # noqa: E402
from src.config import CONFIG  # noqa: E402

MEDIA = ROOT / "artifacts" / "media"
OUT = ROOT / "outputs"


def main() -> None:
    manifest = json.loads((ROOT / "artifacts" / "manifest.json").read_text())
    OUT.mkdir(exist_ok=True)
    segments = []
    per_frame_rows = []

    for entry in manifest["clips"]:
        clip_id = entry["id"]
        ref_y, ref_u, ref_v = video_io.load_yuv(MEDIA / f"{clip_id}-reference.mp4")
        cand_y, cand_u, cand_v = video_io.load_yuv(MEDIA / f"{clip_id}-candidate.mp4")
        measured = pipeline.measure_clip(
            ref_y, ref_u, ref_v, cand_y, cand_u, cand_v
        )
        score = fusion.fused_score(
            measured["fidelity"],
            measured["detailRetention"],
            measured["motionFidelity"],
        )
        ref_score = fusion.reference_score(measured["lumaSsim"])
        row = {
            "id": clip_id,
            "start": entry["start"],
            "end": entry["end"],
            "distortion": entry["distortion"],
            "subjectiveMos": entry["subjectiveMos"],
            "subjectiveNote": entry["subjectiveNote"],
            "fusedScore": score,
            "band": fusion.band_for(score),
            "referenceScore": ref_score,
            "fidelity": measured["fidelity"],
            "detailRetention": measured["detailRetention"],
            "detailLoss": measured["detailLoss"],
            "motionFidelity": measured["motionFidelity"],
            "motionIndex": measured["meanMotionIndex"],
            "meanPoolingWeight": measured["meanPoolingWeight"],
            "lumaSsim": measured["lumaSsim"],
            "chromaSsim": measured["chromaSsim"],
            "weightedPsnrDb": measured["psnrDb"],
            "frameCount": len(measured["frames"]),
        }
        segments.append(row)
        for frame in measured["frames"]:
            per_frame_rows.append(
                {
                    "id": clip_id,
                    "start": entry["start"],
                    "end": entry["end"],
                    "frame": frame["index"],
                    "lumaSsim": frame["lumaSsim"],
                    "chromaSsim": frame["chromaSsim"],
                    "psnrDb": frame["psnrDb"],
                    "fidelity": frame["fidelity"],
                    "detailRetention": frame["detailRetention"],
                    "motionFidelity": frame["motionFidelity"],
                    "motionIndex": frame["motionIndex"],
                }
            )
        print(f"{clip_id}: fused={score:7.3f} ref={ref_score:7.3f} band={row['band']}")

    _rank(segments)
    _write_csv(segments, per_frame_rows)
    (OUT / "segment_results.json").write_text(
        json.dumps(
            {"config": CONFIG, "segments": segments},
            indent=2,
            ensure_ascii=False,
        )
    )


def _rank(segments: list[dict]) -> None:
    order_fused = sorted(segments, key=lambda row: -row["fusedScore"])
    order_ref = sorted(segments, key=lambda row: -row["referenceScore"])
    order_subj = sorted(segments, key=lambda row: -row["subjectiveMos"])
    for position, row in enumerate(order_fused, start=1):
        row["fusedRank"] = position
    for position, row in enumerate(order_ref, start=1):
        row["referenceRank"] = position
    for position, row in enumerate(order_subj, start=1):
        row["subjectiveRank"] = position
    for row in segments:
        row["rankDelta"] = row["fusedRank"] - row["referenceRank"]
        row["absoluteRankDelta"] = abs(row["rankDelta"])


def _write_csv(segments: list[dict], per_frame_rows: list[dict]) -> None:
    segment_fields = list(segments[0].keys())
    with (OUT / "segment_scores.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=segment_fields)
        writer.writeheader()
        writer.writerows(sorted(segments, key=lambda row: row["fusedRank"]))
    with (OUT / "per_frame_scores.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(per_frame_rows[0].keys()))
        writer.writeheader()
        writer.writerows(per_frame_rows)


if __name__ == "__main__":
    main()
