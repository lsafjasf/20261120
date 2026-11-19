"""End-to-end head-rotation -> spatial-audio simulation and measurements.

Runs the same 60 Hz noisy orientation stream through two engines:

* direct mapping (raw yaw applied one-to-one)
* One-Euro smoothed mapping with the Doppler slew limiter

and writes stability, latency, Doppler, angular-velocity/update-frequency
and reverb-tail measurements plus SVG charts into ``artifacts/``.
"""

import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.spatial_audio.engine import SpatialEngine
from src.spatial_audio.filters import DirectFilter, OneEuroFilter
from src.spatial_audio import metrics
from src.spatial_audio.orientation import (
    OrientationSimulator,
    fast_turn_profile,
)
from src.svggen import BarChart, LineChart

ART = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "artifacts")
DURATION = 3.2
SENSOR_HZ = 60.0
RENDER_HZ = 120.0


def build_engine(filter_obj, slew: bool) -> SpatialEngine:
    return SpatialEngine(
        filter_obj,
        source_yaw=0.0,
        source_distance=1.0,
        render_hz=RENDER_HZ,
        doppler_slew_rate=1.5,
        doppler_slew_enabled=slew,
    )


def run_case(name: str, filter_obj, slew: bool, yaw_fn):
    engine = build_engine(filter_obj, slew)
    sim = OrientationSimulator(yaw_fn, sample_rate_hz=SENSOR_HZ)
    events = list(sim.stream(DURATION))
    states = []
    n_ticks = int(DURATION * RENDER_HZ) + 1
    ev_idx = 0
    for i in range(n_ticks):
        now = i / RENDER_HZ
        # Sensor events become available only after their capture time,
        # exactly as on a real device: the queue never holds future samples.
        while ev_idx < len(events) and events[ev_idx].t <= now:
            engine.push_event(events[ev_idx])
            ev_idx += 1
        states.append(engine.render(now))
    return engine, states


def summarize(name, engine, states, yaw_fn, slew):
    return {
        "mode": name,
        "latency_ms": engine.latency_statistics(),
        "static": {
            "raw_jitter_deg": metrics.raw_static_jitter_deg(states, 0.0, 0.5),
            "output_jitter_deg": metrics.static_jitter_deg(states, 0.0, 0.5),
            "pan_step_rms_db": metrics.pan_step_rms_db(states),
        },
        "tracking": metrics.tracking_lag_ms(states, yaw_fn,
                                            during=(0.9, 1.55)),
        "doppler": metrics.doppler_jump_stats(states, slew=slew),
        "render_rate_hz": metrics.render_rate_hz(states),
        "max_queue_depth": metrics.max_queue_depth(states),
    }


def main():
    os.makedirs(ART, exist_ok=True)
    yaw_fn, vel_fn, sigma = fast_turn_profile()

    direct_engine, direct_states = run_case("direct", DirectFilter(), False, yaw_fn)
    smooth_engine, smooth_states = run_case(
        "one_euro", OneEuroFilter(), True, yaw_fn)

    results = {
        "config": {
            "sensor_hz": SENSOR_HZ,
            "render_hz": RENDER_HZ,
            "duration_s": DURATION,
            "turn_sigma_s": sigma,
            "peak_speed_deg_s": math.degrees(vel_fn(1.2)),
        },
        "direct": summarize("direct", direct_engine, direct_states, yaw_fn, False),
        "one_euro": summarize("one_euro", smooth_engine, smooth_states, yaw_fn, True),
    }

    results["update_rate_by_speed"] = {
        "window_s": 0.25,
        "direct": metrics.update_rate_curve(direct_states),
        "one_euro": metrics.update_rate_curve(smooth_states),
    }
    edges = [0, 30, 100, 250, 500]
    summary = []
    for lo, hi in zip(edges, edges[1:]):
        row = {"speed_lo_deg_s": lo, "speed_hi_deg_s": hi,
               "note": "直接映射静止窗口速度受传感器噪声抬高，低速箱可能为空"}
        for mode, label in (("direct", "direct"), ("one_euro", "one_euro")):
            rows = [r for r in results["update_rate_by_speed"][mode]
                    if lo <= r["speed_deg_s"] < hi]
            if rows:
                row[f"{label}_applied_hz"] = round(
                    sum(r["applied_hz"] for r in rows) / len(rows), 2)
                row[f"{label}_changed_hz"] = round(
                    sum(r["changed_hz"] for r in rows) / len(rows), 2)
            else:
                row[f"{label}_applied_hz"] = None
                row[f"{label}_changed_hz"] = None
        summary.append(row)
    results["update_rate_summary"] = summary

    with open(os.path.join(ART, "metrics.json"), "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2, ensure_ascii=False)

    plot_azimuth(direct_states, smooth_states, yaw_fn)
    plot_doppler(direct_states, smooth_states)
    plot_pan_steps(direct_states, smooth_states)
    plot_speed_vs_rate(results["update_rate_by_speed"])
    print(json.dumps(results, indent=2, ensure_ascii=False))
    return results


def plot_azimuth(direct, smooth, yaw_fn):
    chart = LineChart(title="声源相对方位：直接映射 vs One-Euro 平滑",
                      xlabel="时间 t (s)", ylabel="相对方位 (deg)")
    ts = [s.t for s in smooth]
    chart.add_series(ts, [math.degrees(yaw_fn(t)) for t in ts],
                     color="#9ca3af", label="真实方位", dashed=True)
    chart.add_series([s.t for s in direct],
                     [math.degrees(s.filtered_yaw) for s in direct],
                     color="#dc2626", width=1.1, label="直接映射")
    chart.add_series(ts,
                     [math.degrees(s.filtered_yaw) for s in smooth],
                     color="#2563eb", width=1.8, label="One-Euro 平滑")
    chart.save(os.path.join(ART, "azimuth_tracking.svg"))


def plot_doppler(direct, smooth):
    chart = LineChart(title="快速转头时的多普勒音高变化（cents）",
                      xlabel="时间 t (s)", ylabel="音高偏移 (cents)")
    chart.add_series([s.t for s in direct],
                     [1200 * math.log2(s.doppler_target) for s in direct],
                     color="#dc2626", width=1.1, label="直接映射（无限幅）")
    chart.add_series([s.t for s in smooth],
                     [s.doppler_cents for s in smooth],
                     color="#2563eb", width=1.8, label="平滑 + 摆率限制")
    chart.save(os.path.join(ART, "doppler_jump.svg"))


def plot_pan_steps(direct, smooth):
    def steps(states):
        out, prev = [], None
        for s in states:
            if prev is not None:
                out.append(20 * math.log10(max(s.gain_l, 1e-6)
                                            / max(prev, 1e-6)))
            prev = s.gain_l
        return out
    chart = BarChart(title="静态下方位更新的单拍声像跳变 (RMS dB)",
                     xlabel="更新方式", ylabel="每渲染拍增益变化 RMS (dB)")
    chart.add_bar("直接映射",
                  metrics.pan_step_rms_db(
                      [s for s in direct if s.t <= 0.5]), "#dc2626")
    chart.add_bar("One-Euro 平滑",
                  metrics.pan_step_rms_db(
                      [s for s in smooth if s.t <= 0.5]), "#2563eb")
    chart.save(os.path.join(ART, "pan_zipper.svg"))


def plot_speed_vs_rate(rate_table):
    chart = LineChart(title="旋转角速度与更新频率的关系",
                      xlabel="窗口内平均角速度 (deg/s)",
                      ylabel="更新频率 (Hz)")
    d, s = rate_table["direct"], rate_table["one_euro"]
    chart.add_series([r["speed_deg_s"] for r in d],
                     [r["applied_hz"] for r in d],
                     color="#7f1d1d", label="直接映射：事件应用率")
    chart.add_series([r["speed_deg_s"] for r in s],
                     [r["applied_hz"] for r in s],
                     color="#1e3a8a", label="平滑：事件应用率")
    chart.add_series([r["speed_deg_s"] for r in s],
                     [r["changed_hz"] for r in s],
                     color="#2563eb", dashed=True, label="平滑：参数实际变化率")
    chart.add_series([r["speed_deg_s"] for r in d],
                     [r["changed_hz"] for r in d],
                     color="#dc2626", dashed=True, label="直接映射：参数实际变化率")
    chart.save(os.path.join(ART, "speed_vs_rate.svg"))


if __name__ == "__main__":
    main()
