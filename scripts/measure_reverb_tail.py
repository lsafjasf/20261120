"""Reverb tail verification for low / mid / high RT60 settings.

Renders a dry log-sweep through the Schroeder reverb at three RT60 values
and measures the actual decay slope (Schroeder backward-integration of the
energy curve), endpoint level and the largest sample-to-sample jump inside
the final 10 ms. A smooth, fully-released tail proves low-RT60 sounds are
never cut off abruptly.
"""

import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.spatial_audio.reverb import Reverb
from src.spatial_audio.wavio import write_wav, sine_sweep
from src.svggen import LineChart

ART = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "artifacts")
SAMPLE_RATE = 16000
RT60_LIST = (0.08, 0.25, 0.60)


def energy_curve_db(samples, hop_ms=2.0):
    """RMS level in dBFS of successive short windows."""
    hop = max(1, int(hop_ms / 1000.0 * SAMPLE_RATE))
    ts, levels = [], []
    for start in range(0, len(samples) - hop, hop):
        frame = samples[start:start + hop]
        rms = math.sqrt(sum(v * v for v in frame) / len(frame))
        ts.append(start / SAMPLE_RATE)
        levels.append(20.0 * math.log10(max(rms, 1e-9)))
    return ts, levels


def measured_rt60(tail_ts, tail_db, dry_end_s):
    """Placeholder kept for API compatibility; see :func:`schroeder_rt60`."""
    return float("nan")


def schroeder_rt60(samples, sr, start_s):
    """RT60 from the Schroeder backward-integrated energy curve (ISO 3382).

    Windowed RMS of a comb-based reverb shows beating; backward integration
    produces a smooth, monotonically decaying envelope whose dB slope gives
    an unbiased RT60 estimate.
    """
    start = int(start_s * sr)
    tail = samples[start:]
    energy = [v * v for v in tail]
    integrated = []
    acc = 0.0
    for e in reversed(energy):
        acc += e
        integrated.append(acc)
    integrated.reverse()
    peak = integrated[0] or 1e-20
    db = [10.0 * math.log10(max(v / peak, 1e-12)) for v in integrated]
    ts = [start / sr + i / sr for i in range(len(db))]

    # Fit the clean -5..-25 dB segment: it avoids both the early-reflection
    # build-up near 0 dB and the noise floor at the very end, which matters
    # for very short (low-RT60) tails.
    pts = [(t, y) for t, y in zip(ts, db) if -25.0 <= y <= -5.0]
    if len(pts) < 8:
        pts = [(t, y) for t, y in zip(ts, db) if -35.0 <= y <= -5.0]
    n = len(pts)
    if n < 3:
        return float("nan"), ts, db
    sx = sum(t for t, _ in pts)
    sy = sum(y for _, y in pts)
    sxx = sum(t * t for t, _ in pts)
    sxy = sum(t * y for t, y in pts)
    slope = (n * sxy - sx * sy) / (n * sxx - sx * sx)
    return (-60.0 / slope if slope < 0 else float("nan")), ts, db


def final_jump(output):
    tail = output[-int(0.01 * SAMPLE_RATE):]
    return max(abs(b - a) for a, b in zip(tail, tail[1:]))


def main():
    os.makedirs(ART, exist_ok=True)
    dry = sine_sweep(0.3, SAMPLE_RATE)
    chart = LineChart(title="混响尾音衰减曲线（不同 RT60）",
                      xlabel="时间 t (s)", ylabel="能量 (dBFS)")
    chart.set_ylim(-90, 0)
    colors = {0.08: "#16a34a", 0.25: "#2563eb", 0.60: "#7c3aed"}
    report = {}

    for rt60 in RT60_LIST:
        reverb = Reverb(SAMPLE_RATE, rt60_s=rt60, wet=0.5)
        out = reverb.process(dry)
        ts, db = energy_curve_db(out)
        chart.add_series(ts, db, color=colors[rt60],
                         label=f"RT60={rt60:.2f}s")
        measured, sch_ts, sch_db = schroeder_rt60(out, SAMPLE_RATE, 0.3)
        write_wav(os.path.join(ART, f"reverb_rt{int(rt60*1000):03d}ms.wav"),
                  [out], SAMPLE_RATE)
        report[f"rt60_{rt60:.2f}s"] = {
            "target_rt60_s": rt60,
            "measured_rt60_s": round(measured, 4),
            "tail_endpoint_dbfs": round(reverb.tail_endpoint_db(out), 2),
            "final_10ms_max_jump": round(final_jump(out), 8),
            "tail_duration_s": round(len(out) / SAMPLE_RATE - 0.3, 3),
            "total_duration_s": round(len(out) / SAMPLE_RATE, 3),
        }
        # Overlay the Schroeder envelope only once per setting (denser data).
        chart.add_series(sch_ts, sch_db, color=colors[rt60], width=1.0,
                         dashed=True, label=f"RT60={rt60:.2f}s 能量积分")

    chart.save(os.path.join(ART, "reverb_decay_tail.svg"))
    with open(os.path.join(ART, "reverb_metrics.json"), "w",
              encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
