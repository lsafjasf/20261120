"""Parametric EQ analysis: design vs measured response, click-free switching,
Q-sweep peak invariance, center-frequency error and Nyquist phase response."""

from __future__ import annotations

import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from .biquad import (
    BiquadCoeffs,
    crossfade_switch,
    design_biquad,
    design_response,
    process_cascade,
)
from .measure import (
    magnitude_peak_freq_db,
    probed_response,
)

FS = 48000
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "outputs")


def hz_label(f: float) -> str:
    return f"{f:,.0f} Hz"


def build_banks() -> dict[str, list[BiquadCoeffs]]:
    return {
        "peaking": [
            design_biquad("peaking", fc=1000.0, fs=FS, gain_db=6.0, q=2.0),
        ],
        "shelving": [
            design_biquad("low_shelf", fc=200.0, fs=FS, gain_db=6.0, q=1.0),
            design_biquad("high_shelf", fc=8000.0, fs=FS, gain_db=-6.0, q=1.0),
        ],
        "combined": [
            design_biquad("low_shelf", fc=200.0, fs=FS, gain_db=6.0, q=1.0),
            design_biquad("peaking", fc=1000.0, fs=FS, gain_db=6.0, q=2.0),
            design_biquad("high_shelf", fc=8000.0, fs=FS, gain_db=-6.0, q=1.0),
        ],
    }


def design_vs_measured(banks: dict[str, list[BiquadCoeffs]]) -> dict:
    """Plot and quantify design vs measured magnitude/phase for each bank."""
    f_grid = np.geomspace(25.0, FS / 2.0 - 100.0, 768)
    report = {}

    fig_mag, axes_mag = plt.subplots(3, 1, figsize=(10, 11), sharex=True)
    fig_phase, axes_phase = plt.subplots(3, 1, figsize=(10, 11), sharex=True)

    for ax_m, ax_p, (name, bank) in zip(axes_mag, axes_phase, banks.items()):
        h_design = design_response(bank, f_grid, FS)
        # Steady-state sine probing at the exact design frequency grid.
        h_meas = probed_response(bank, FS, f_grid)
        mag_grid = 20 * np.log10(np.maximum(np.abs(h_meas), 1e-12))
        ph_grid = np.unwrap(np.angle(h_meas))
        design_mag_db = 20 * np.log10(np.abs(h_design))
        design_phase = np.unwrap(np.angle(h_design))

        err = mag_grid - design_mag_db
        band = (f_grid >= 30) & (f_grid <= FS / 2 * 0.98)
        report[name] = {
            "mag_error_mean_db": float(np.mean(err[band])),
            "mag_error_max_abs_db": float(np.max(np.abs(err[band]))),
        }

        ax_m.semilogx(f_grid, design_mag_db, label="design (coeffs)")
        ax_m.semilogx(f_grid, mag_grid, "--", label="measured (sine probe)")
        ax_m.set_title(f"{name} magnitude")
        ax_m.set_ylabel("dB")
        ax_m.grid(True, which="both", alpha=0.4)
        ax_m.legend(loc="best", fontsize=9)

        ax_p.semilogx(f_grid, np.degrees(design_phase), label="design")
        ax_p.semilogx(f_grid, np.degrees(ph_grid), "--", label="measured")
        ax_p.set_title(f"{name} phase")
        ax_p.set_ylabel("degrees")
        ax_p.grid(True, which="both", alpha=0.4)
        ax_p.legend(loc="best", fontsize=9)

    for ax in (axes_mag[-1], axes_phase[-1]):
        ax.set_xlabel("frequency (Hz)")
    fig_mag.tight_layout()
    fig_phase.tight_layout()
    fig_mag.savefig(os.path.join(OUT_DIR, "01_design_vs_measured_magnitude.png"), dpi=130)
    fig_phase.savefig(os.path.join(OUT_DIR, "02_design_vs_measured_phase.png"), dpi=130)
    plt.close("all")
    return report


def click_switch_test() -> dict:
    """Switch peaking parameters mid-signal; compare hard switch vs crossfade."""
    duration = 2.0
    n = int(duration * FS)
    t = np.arange(n) / FS
    # Steady 1 kHz tone: both banks reach steady state before the switch, so
    # any discontinuity at the boundary is purely a parameter-switch artifact.
    x = 0.5 * np.sin(2.0 * np.pi * 1000.0 * t)
    switch = n // 2
    fade = int(0.01 * FS)  # 10 ms crossfade

    # Deliberately large parameter change (gain sign reversal) to maximize the
    # discontinuity a naive hard switch would produce.
    old = [design_biquad("peaking", 1000.0, FS, 12.0, 2.0)]
    new = [design_biquad("peaking", 2500.0, FS, -18.0, 4.0)]

    # Hard switch: run old bank then new bank with an abrupt state reset.
    y_old = process_cascade(x[:switch], old)
    y_new = process_cascade(x[switch:], new)
    hard = np.concatenate([y_old, y_new])
    smooth = crossfade_switch(x, old, new, switch, fade)

    # Metric 1: exact boundary jump, normalized to the steady-state step level.
    baseline_step = float(np.median(np.abs(np.diff(smooth[: switch - 4 * fade]))))
    hard_jump = float(abs(hard[switch] - hard[switch - 1]))
    smooth_jump = float(abs(smooth[switch] - smooth[switch - 1]))
    # Metric 2: max step in the tight +-10-sample neighborhood of the boundary.
    local = slice(switch - 10, switch + 10)
    hard_step = float(np.max(np.abs(np.diff(hard[local]))))
    smooth_step = float(np.max(np.abs(np.diff(smooth[local]))))
    # Metric 3: high-frequency residual energy around the switch (click energy).
    def click_energy(y: np.ndarray) -> float:
        seg = y[switch - 2 * fade : switch + 2 * fade]
        spec = np.fft.rfft(seg * np.hanning(len(seg)))
        freqs = np.fft.rfftfreq(len(seg), 1.0 / FS)
        return float(np.sum(np.abs(spec[freqs > 4000]) ** 2))

    hard_e = click_energy(hard)
    smooth_e = click_energy(smooth)

    fig, ax = plt.subplots(2, 1, figsize=(10, 7), sharex=True)
    zoom = slice(switch - 5 * fade, switch + 5 * fade)
    ax[0].plot(t[zoom], hard[zoom])
    ax[0].axvline(switch / FS, color="r", ls=":", label="parameter switch")
    ax[0].set_title("hard switch (state reset) — audible click")
    ax[0].set_ylabel("amplitude")
    ax[0].legend()
    ax[0].grid(alpha=0.4)
    ax[1].plot(t[zoom], smooth[zoom])
    ax[1].axvline(switch / FS, color="r", ls=":")
    ax[1].set_title("10 ms crossfade — continuous waveform")
    ax[1].set_ylabel("amplitude")
    ax[1].set_xlabel("time (s)")
    ax[1].grid(alpha=0.4)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, "03_switch_click_test.png"), dpi=130)
    plt.close(fig)

    return {
        "baseline_sample_step": baseline_step,
        "hard_switch_boundary_jump": hard_jump,
        "crossfade_boundary_jump": smooth_jump,
        "hard_jump_to_baseline_ratio": hard_jump / baseline_step,
        "crossfade_jump_to_baseline_ratio": smooth_jump / baseline_step,
        "hard_switch_max_step": hard_step,
        "crossfade_max_step": smooth_step,
        "hard_click_energy": hard_e,
        "crossfade_click_energy": smooth_e,
        "step_suppression_db": float(20 * np.log10(hard_step / max(smooth_step, 1e-15))),
        "click_energy_suppression_db": float(
            10 * np.log10(hard_e / max(smooth_e, 1e-30))
        ),
        "crossfade_ms": 10.0,
    }


def parameter_sweeps() -> None:
    """Response families while varying center frequency, gain and Q separately."""
    f_grid = np.geomspace(20.0, FS / 2.0 - 50.0, 2048)
    fig, axes = plt.subplots(3, 1, figsize=(10, 12))

    for fc in (250.0, 1000.0, 4000.0):
        h = design_response([design_biquad("peaking", fc, FS, 6.0, 2.0)], f_grid, FS)
        axes[0].semilogx(f_grid, 20 * np.log10(np.abs(h)), label=f"fc={hz_label(fc)}")
    axes[0].set_title("center-frequency sweep (gain=+6 dB, Q=2)")

    for g in (-12.0, -6.0, 6.0, 12.0):
        h = design_response([design_biquad("peaking", 1000.0, FS, g, 2.0)], f_grid, FS)
        axes[1].semilogx(f_grid, 20 * np.log10(np.abs(h)), label=f"gain={g:+.0f} dB")
    axes[1].set_title("gain sweep (fc=1 kHz, Q=2)")

    for q in (0.7, 2.0, 5.0, 10.0):
        h = design_response([design_biquad("peaking", 1000.0, FS, 6.0, q)], f_grid, FS)
        axes[2].semilogx(f_grid, 20 * np.log10(np.abs(h)), label=f"Q={q:g}")
    axes[2].set_title("Q sweep (fc=1 kHz, gain=+6 dB)")

    for ax in axes:
        ax.set_xlabel("frequency (Hz)")
        ax.set_ylabel("dB")
        ax.grid(True, which="both", alpha=0.4)
        ax.legend(fontsize=9)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, "04_parameter_sweeps.png"), dpi=130)
    plt.close(fig)


def center_freq_error_and_peak_invariance() -> dict:
    """Compare designed vs measured peak frequency; verify Q does not move
    the gain extremum of a peaking filter."""
    design_fcs = np.array([250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0])
    rows = []
    for fc in design_fcs:
        bank = [design_biquad("peaking", fc, FS, 6.0, 2.0)]
        # Dense local probe grid around fc to locate the measured extremum.
        f_meas = np.geomspace(fc * 0.5, min(fc * 2.0, FS / 2 * 0.98), 400)
        h_meas = probed_response(bank, FS, f_meas, tone_s=0.6, settle_s=0.2)
        lo, hi = fc * 0.5, fc * 2.0
        peak_f, peak_db = magnitude_peak_freq_db(f_meas, h_meas, band=(lo, hi))
        rows.append({
            "design_fc_hz": float(fc),
            "measured_peak_hz": peak_f,
            "abs_error_hz": abs(peak_f - fc),
            "rel_error_pct": 100.0 * abs(peak_f - fc) / fc,
            "measured_peak_gain_db": peak_db,
        })

    # Q sweep: the analytic extremum of RBJ peaking sits exactly at fc with
    # magnitude == gain_db, independent of Q. Verify numerically.
    q_rows = []
    f_grid = np.geomspace(50.0, FS / 2.0 - 100.0, 1 << 16)
    for q in (0.5, 1.0, 2.0, 4.0, 8.0, 16.0):
        coeffs = design_biquad("peaking", 1000.0, FS, 6.0, q)
        h = design_response([coeffs], f_grid, FS)
        mag = 20 * np.log10(np.abs(h))
        idx = int(np.argmax(mag))
        q_rows.append({
            "q": q,
            "extremum_freq_hz": float(f_grid[idx]),
            "extremum_gain_db": float(mag[idx]),
        })

    fig, ax = plt.subplots(1, 2, figsize=(12, 4.6))
    ax[0].plot(design_fcs, [r["rel_error_pct"] for r in rows], "o-")
    ax[0].set_xlabel("design fc (Hz)")
    ax[0].set_ylabel("center-frequency error (%)")
    ax[0].set_xscale("log")
    ax[0].grid(True, which="both", alpha=0.4)
    ax[0].set_title("measured vs design center frequency")
    ax[1].plot([r["q"] for r in q_rows],
               [r["extremum_gain_db"] for r in q_rows], "s-")
    ax[1].axhline(6.0, color="r", ls=":", label="design gain +6 dB")
    ax[1].set_xlabel("Q")
    ax[1].set_ylabel("peak gain (dB)")
    ax[1].grid(alpha=0.4)
    ax[1].legend()
    ax[1].set_title("gain extremum vs Q (must stay flat)")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, "05_fc_error_and_q_invariance.png"), dpi=130)
    plt.close(fig)

    gains = np.array([r["extremum_gain_db"] for r in q_rows])
    freqs = np.array([r["extremum_freq_hz"] for r in q_rows])
    return {
        "center_frequency": rows,
        "q_sweep": q_rows,
        "peak_gain_spread_db": float(np.max(gains) - np.min(gains)),
        "peak_freq_spread_hz": float(np.max(freqs) - np.min(freqs)),
    }


def nyquist_phase() -> dict:
    """Phase response near Nyquist for the combined bank."""
    bank = build_banks()["combined"]
    f_grid = np.linspace(FS * 0.40, FS / 2.0, 4096)
    h = design_response(bank, f_grid, FS)
    phase = np.degrees(np.unwrap(np.angle(h)))

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(f_grid, phase)
    ax.axvline(FS / 2.0, color="r", ls=":", label=f"Nyquist = {FS // 2} Hz")
    ax.set_xlabel("frequency (Hz)")
    ax.set_ylabel("unwrapped phase (degrees)")
    ax.set_title("phase response near Nyquist (combined EQ)")
    ax.grid(alpha=0.4)
    ax.legend()
    fig.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, "06_nyquist_phase.png"), dpi=130)
    plt.close(fig)

    at_nyq = design_response(bank, np.array([FS / 2.0]), FS)[0]
    return {
        "nyquist_hz": FS / 2.0,
        "phase_at_nyquist_deg": float(np.degrees(np.angle(at_nyq))),
        "magnitude_at_nyquist_db": float(20 * np.log10(np.abs(at_nyq))),
        "phase_at_0p9_nyquist_deg": float(
            np.degrees(np.angle(design_response(bank, np.array([FS * 0.45]), FS)[0]))
        ),
    }


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    banks = build_banks()
    report = {
        "fs_hz": FS,
        "coefficients": {
            name: [c.__dict__ for c in bank] for name, bank in banks.items()
        },
        "design_vs_measured": design_vs_measured(banks),
        "switch_click": click_switch_test(),
    }
    parameter_sweeps()
    report["fc_and_q"] = center_freq_error_and_peak_invariance()
    report["nyquist_phase"] = nyquist_phase()
    with open(os.path.join(OUT_DIR, "eq_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(json.dumps(report, indent=2, ensure_ascii=False)[:4000])


if __name__ == "__main__":
    main()
