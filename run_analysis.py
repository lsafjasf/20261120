"""Binaural / acoustics verification suite.

1. HRTF interpolation + crossfade: azimuth error and phase continuity.
2. Head-rotation events: relative-azimuth update, no phase inversion on
   fast head turns, interpolated azimuth varies without jumps.
3. Air absorption vs frequency (ISO 9613-1) physical-model conformance.
4. Variable-speed (time-stretch) matching-offset linearity.
"""
import json

import numpy as np

import air_absorption as aa
import dynamic_render as dr
import hrtf_model as hm
import time_stretch_check as ts

FS = hm.FS
REPORT = {}


def part1_interpolation():
    """Interpolate between two azimuths; report azimuth error + phase."""
    grid_az, hrirs = hm.build_grid(step_deg=15.0)
    rows = []
    for target in np.arange(-82.5, 82.6, 7.5):
        (hl, hr), w, idx = hm.interp_hrir(target, grid_az, hrirs)
        est = hm.azimuth_from_itd(hm.estimate_itd(hl, hr))
        rows.append((float(target), float(est), float(est - target)))
    errs = np.array([r[2] for r in rows])
    ests = np.array([r[1] for r in rows])
    steps = np.abs(np.diff(ests))
    # Phase continuity across the interpolation sweep at 1 kHz (left ear).
    freqs = np.fft.rfftfreq(hm.HRIR_LEN, 1.0 / FS)
    k1 = int(np.argmin(np.abs(freqs - 1000.0)))
    phases = []
    for target, _, _ in rows:
        (hl, _), _, _ = hm.interp_hrir(target, grid_az, hrirs)
        phases.append(np.angle(np.fft.rfft(hl)[k1]))
    phases = np.unwrap(np.array(phases))
    max_phase_step = float(np.max(np.abs(np.diff(phases))))
    REPORT["interpolation"] = {
        "max_az_error_deg": float(np.max(np.abs(errs))),
        "rms_az_error_deg": float(np.sqrt(np.mean(errs**2))),
        "max_est_step_deg": float(np.max(steps)),
        "azimuth_continuous": bool(np.max(steps) < 2.0 * 7.5),
        "max_phase_step_rad_1khz": max_phase_step,
        "phase_continuous": bool(max_phase_step < np.pi / 2),
        "rows": rows,
    }
    return rows


def part2_head_rotation():
    """Head-rotation events update relative azimuth; fast-turn phase check."""
    grid_az, hrirs = hm.build_grid(step_deg=15.0)
    dur = 3.0
    t = np.arange(int(FS * dur)) / FS
    f0 = 2000.0
    src = np.sin(2 * np.pi * f0 * t)
    # Head yaw events: slow drift, then a fast 90 deg turn in 0.2 s
    # (450 deg/s), hold, then a fast return.
    events = [(0.8, 15.0), (1.0, 105.0), (2.0, 105.0), (2.25, -30.0), (3.0, -30.0)]
    yaw = dr.head_yaw_trajectory(t, events)
    source_world_az = 45.0
    rel_az = dr.wrap180(source_world_az - yaw)
    # Relative azimuth must be continuous (no jumps after wrapping).
    az_steps = np.abs(np.diff(rel_az))
    max_az_step = float(np.max(az_steps))
    left, right = dr.render_dynamic(src, rel_az, grid_az, hrirs)
    pc_l = dr.phase_continuity(left, FS, f0)
    pc_r = dr.phase_continuity(right, FS, f0)
    # Restrict inversion check to the fast-turn windows.
    fast = ((t >= 0.8) & (t <= 1.1)) | ((t >= 2.0) & (t <= 2.35))
    mask_l = fast[:-1] & pc_l["valid"]
    mask_r = fast[:-1] & pc_r["valid"]
    inv_fast_l = int(np.sum(pc_l["inst_w"][mask_l] < 0))
    inv_fast_r = int(np.sum(pc_r["inst_w"][mask_r] < 0))
    bnd_l, _ = dr.block_boundary_phase(left, FS, f0)
    REPORT["head_rotation"] = {
        "max_rel_az_step_deg_per_sample": max_az_step,
        "rel_az_continuous": bool(max_az_step < 1.0),
        "max_phase_jump_rad_left": pc_l["max_jump_rad"],
        "max_phase_jump_rad_right": pc_r["max_jump_rad"],
        "phase_inversions_fast_turn_left": inv_fast_l,
        "phase_inversions_fast_turn_right": inv_fast_r,
        "no_phase_inversion": bool(inv_fast_l == 0 and inv_fast_r == 0),
        "max_block_boundary_phase_err_rad": float(bnd_l),
    }


def part3_air_absorption():
    """Air absorption table vs frequency + physical-model checks."""
    freqs, alpha_dbm, alpha_dbkm = aa.absorption_table()
    checks = aa.physical_model_checks(freqs, alpha_dbm)
    REPORT["air_absorption"] = {
        "conditions": "20 C, 101.325 kPa, 70 % RH (ISO 9613-1)",
        "table": [
            {"freq_hz": float(f), "alpha_db_per_m": float(a),
             "alpha_db_per_km": float(ak)}
            for f, a, ak in zip(freqs, alpha_dbm, alpha_dbkm)
        ],
        **checks,
    }


def part4_time_stretch():
    """Variable-speed matching-offset linearity."""
    results = ts.check_linearity(fs=FS)
    REPORT["time_stretch"] = {
        "rates": results,
        "linear": bool(all(r["r2"] > 0.999 and abs(r["slope_err"]) < 0.01
                           for r in results)),
    }


def print_report():
    p = print
    p("=" * 64)
    p("1) HRTF interpolation / crossfade (15 deg grid, equal-power)")
    p("-" * 64)
    r = REPORT["interpolation"]
    p(f"  azimuth error: max {r['max_az_error_deg']:.2f} deg, "
      f"RMS {r['rms_az_error_deg']:.2f} deg")
    p(f"  azimuth continuity: max step {r['max_est_step_deg']:.2f} deg "
      f"-> continuous: {r['azimuth_continuous']}")
    p(f"  phase continuity @1 kHz: max step "
      f"{r['max_phase_step_rad_1khz']:.4f} rad "
      f"-> continuous: {r['phase_continuous']}")
    p("  target -> estimated azimuth (deg):")
    for tgt, est, err in r["rows"]:
        p(f"    {tgt:7.1f} -> {est:7.2f}   (err {err:+.2f})")
    p("=" * 64)
    p("2) Head-rotation events (fast turns: 90 deg/0.2 s, 135 deg/0.25 s)")
    p("-" * 64)
    r = REPORT["head_rotation"]
    p(f"  relative-azimuth max step: {r['max_rel_az_step_deg_per_sample']:.4f} "
      f"deg/sample -> continuous: {r['rel_az_continuous']}")
    p(f"  max phase jump L/R: {r['max_phase_jump_rad_left']:.4f} / "
      f"{r['max_phase_jump_rad_right']:.4f} rad")
    p(f"  phase inversions during fast turns L/R: "
      f"{r['phase_inversions_fast_turn_left']} / "
      f"{r['phase_inversions_fast_turn_right']} "
      f"-> no inversion: {r['no_phase_inversion']}")
    p(f"  block-boundary phase error: "
      f"{r['max_block_boundary_phase_err_rad']:.4f} rad")
    p("=" * 64)
    p("3) Air absorption vs frequency ("
      + REPORT["air_absorption"]["conditions"] + ")")
    p("-" * 64)
    p(f"  {'f [Hz]':>8} {'alpha [dB/m]':>14} {'alpha [dB/km]':>14}")
    for row in REPORT["air_absorption"]["table"]:
        p(f"  {row['freq_hz']:8.0f} {row['alpha_db_per_m']:14.6f} "
          f"{row['alpha_db_per_km']:14.3f}")
    r = REPORT["air_absorption"]
    p(f"  monotonic increase: {r['monotonic_increase']}")
    p(f"  alpha/f^2 HF asymptote rel. spread: "
      f"{r['hf_alpha_over_f2_rel_spread']:.3f}")
    p(f"  1 kHz: {r['alpha_1khz_db_per_km']:.2f} dB/km, "
      f"10 kHz: {r['alpha_10khz_db_per_km']:.1f} dB/km "
      f"-> physical range: {r['range_ok']}")
    p("=" * 64)
    p("4) Variable-speed (time-stretch) matching-offset linearity")
    p("-" * 64)
    for r in REPORT["time_stretch"]["rates"]:
        p(f"  rate {r['rate']:.2f}: slope {r['slope']:+.4f} "
          f"(expected {r['expected_slope']:+.2f}, err {r['slope_err']:+.4f}), "
          f"R^2 {r['r2']:.6f}, {r['n_windows']} windows")
    p(f"  offsets linear: {REPORT['time_stretch']['linear']}")
    p("=" * 64)


def main():
    part1_interpolation()
    part2_head_rotation()
    part3_air_absorption()
    part4_time_stretch()
    print_report()
    with open("artifacts/analysis_report.json", "w") as fh:
        json.dump(REPORT, fh, indent=2)
    ok = all([
        REPORT["interpolation"]["azimuth_continuous"],
        REPORT["interpolation"]["phase_continuous"],
        REPORT["head_rotation"]["rel_az_continuous"],
        REPORT["head_rotation"]["no_phase_inversion"],
        REPORT["air_absorption"]["monotonic_increase"],
        REPORT["air_absorption"]["range_ok"],
        REPORT["time_stretch"]["linear"],
    ])
    print(f"ALL CHECKS PASSED: {ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
