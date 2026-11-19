"""Run the deterministic retransmission scenario and emit a verification report."""
from __future__ import annotations

import json
import os

from srt.retrans_scenario import run_retrans_scenario


def contiguous(seq_list, n):
    return seq_list == list(range(n))


def main():
    n = 24
    lost = [3, 7, 12]
    result = run_retrans_scenario(n_packets=n, lost_seqs=tuple(lost),
                                  drop_kmrsp=1)
    a, b = result["a"], result["b"]
    retrans = {name: [s for ep, batch in result["retrans_log"]
                      if ep == name for s in batch]
               for name in ("A", "B")}

    checks = {
        "kmrsp_drop_recovered": result["dropped_kmrsp"] == 1
        and a.km_ok and b.km_ok and len(result["kmrsp_times"]) >= 1,
        "delivery_A_contiguous": contiguous(a.delivered, n),
        "delivery_B_contiguous": contiguous(b.delivered, n),
        "no_duplicate_delivery": a.dup_deliveries == 0 and b.dup_deliveries == 0,
        "no_acked_resend": a.acked_resend_attempts == 0
        and b.acked_resend_attempts == 0,
        "lost_seqs_retransmitted": all(s in retrans["A"] for s in lost),
        "session_completed": a.state.name == "ESTABLISHED"
        and b.state.name == "ESTABLISHED",
    }
    report = {
        "scenario": "deterministic loss + KMRSP drop + retransmission",
        "sim_ms": round(result["sim_ms"], 1),
        "forced_dropped_data": result["dropped_data"],
        "forced_dropped_kmrsp": result["dropped_kmrsp"],
        "kmrsp_arrival_times_ms": result["kmrsp_times"],
        "km_ok": {"A": a.km_ok, "B": b.km_ok},
        "retrans_window_seqs": retrans,
        "retrans_counts": {
            "A": {"nak": a.retrans_by_nak, "rto": a.retrans_by_rto},
            "B": {"nak": b.retrans_by_nak, "rto": b.retrans_by_rto}},
        "delivery": {
            "A": {"count": len(a.delivered), "dups": a.dup_deliveries,
                  "in_order": a.delivered == sorted(a.delivered)},
            "B": {"count": len(b.delivered), "dups": b.dup_deliveries,
                  "in_order": b.delivered == sorted(b.delivered)}},
        "acked_resend_attempts": {"A": a.acked_resend_attempts,
                                  "B": b.acked_resend_attempts},
        "checks": checks,
        "all_pass": all(checks.values()),
    }
    out_dir = os.path.join(os.path.dirname(__file__), "reports")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "retrans-report.json"), "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["all_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
