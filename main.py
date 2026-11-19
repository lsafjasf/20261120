"""End-to-end pipeline: keyword wake-up experiment + figures + AEC report."""

from __future__ import annotations

import json
from pathlib import Path

from kws.aec import run_aec_experiment
from kws.experiment import run as run_kws
from kws.plots import make_all

OUT = Path("outputs")


def main() -> None:
    OUT.mkdir(exist_ok=True)
    results = run_kws(output_dir=OUT)
    aec = make_all(OUT / "metrics.json", OUT)

    aec_summary = {
        "erle_pre_change_db": aec.erle_pre,
        "erle_transient_0_1s_db": aec.erle_transient,
        "erle_reconverged_last_3s_db": aec.erle_reconverged,
        "reconvergence_time_s": aec.reconverge_time_s,
        "echo_path_change_at_s": aec.change_sample / 8000,
        "misalignment_converged_db": aec.misalignment_converged_db,
        "misalignment_peak_after_change_db":
            aec.misalignment_peak_after_change_db,
    }
    with open(OUT / "aec_metrics.json", "w", encoding="utf-8") as fh:
        json.dump(aec_summary, fh, indent=2)

    print("\n=== Summary ===")
    for kind, kw in results["keywords"].items():
        name = f"keyword_{'AB'[int(kind)]}"
        cal = results["calibration"][kind]
        print(f"\n[{name}]")
        print(f"  distance threshold : {cal['distance_threshold']:.4f}")
        print(f"  confidence threshold: {cal['confidence_threshold']:.4f}")
        print(f"  AUC={kw['roc']['auc']:.4f}  EER={kw['roc']['eer']:.4f}")
        print(f"  false wake-ups: {kw['false_wake']['false_wake_ups']}"
              f" / {kw['false_wake']['n_negative_events']} negative events")
        for cond, stats in kw["by_condition"].items():
            print(f"    {cond:8s} gated recall={stats['gated_recall']:.3f}"
                  f"  d'={stats['d_prime']:.2f}")
    print("\nCross-trigger:",
          {k: v["cross_trigger_rate"]
           for k, v in results["cross_trigger"].items()})
    print("\nAEC:", json.dumps(aec_summary, indent=2))
    print(f"\nFigures and metrics written to {OUT}/")


if __name__ == "__main__":
    main()
