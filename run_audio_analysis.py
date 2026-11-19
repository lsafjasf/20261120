"""24->16 bit dither + noise-shaping evaluation.

Produces spectra, residual-noise files, audible low-level samples and a
metrics summary under artifacts/. Run: python3 run_audio_analysis.py
"""
from __future__ import annotations

import json
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from audio_metrics import (
    band_rms_dbfs,
    noise_floor_dbfs,
    residual,
    spectrum_db,
    thd_db,
    write_wav16,
)
from dither_quant import quantize_24_to_16

FS = 44100
TONE_HZ = 997.0
DUR = 4.0
THD_THRESHOLD_DB = -80.0  # given threshold: shaped THD must stay below this
LF_BAND = (20.0, 4000.0)  # audible low band for residue check
HF_BAND = (12000.0, 20000.0)
OUT = "artifacts"

VARIANTS = {
    "A_plain": dict(dither=False, shaper="none"),
    "B_tpdf": dict(dither=True, shaper="none"),
    "C_tpdf_shaped": dict(dither=True, shaper="third_order"),
}


def make_tone(level_dbfs: float) -> np.ndarray:
    t = np.arange(int(FS * DUR)) / FS
    return (10.0 ** (level_dbfs / 20.0)) * np.sin(2 * np.pi * TONE_HZ * t)


def plot_spectra(spectra: dict, title: str, path: str, fmax=22050.0):
    plt.figure(figsize=(10, 5))
    for label, (f, psd) in spectra.items():
        plt.semilogx(f, psd, label=label, linewidth=0.9)
    plt.xlim(20, fmax)
    plt.ylim(-180, 0)
    plt.grid(True, which="both", alpha=0.3)
    plt.xlabel("Hz")
    plt.ylabel("dBFS/Hz")
    plt.title(title)
    plt.legend()
    plt.tight_layout()
    plt.savefig(path, dpi=120)
    plt.close()


def run_level(level_dbfs: float, tag: str) -> dict:
    x = make_tone(level_dbfs)
    results = {}
    sig_spec, res_spec = {}, {}
    for name, kw in VARIANTS.items():
        y = quantize_24_to_16(x, seed=7, **kw)
        r = residual(y, x)
        sig_spec[name] = spectrum_db(y, FS)
        res_spec[name] = spectrum_db(r, FS)
        results[name] = {
            "thd_db": thd_db(y, FS, TONE_HZ),
            "noise_floor_dbfs_hz": noise_floor_dbfs(r, FS),
            "lf_residue_dbfs": band_rms_dbfs(r, FS, *LF_BAND),
            "hf_residue_dbfs": band_rms_dbfs(r, FS, *HF_BAND),
        }
        if tag == "lowlevel":
            write_wav16(os.path.join(OUT, f"audio_{tag}_{name}.wav"), y, FS)
            write_wav16(
                os.path.join(OUT, f"residual_{tag}_{name}.wav"),
                r * 100.0,  # +40 dB so the residual is audible
                FS,
            )
    plot_spectra(
        sig_spec,
        f"Signal spectrum, 997 Hz @ {level_dbfs} dBFS",
        os.path.join(OUT, f"spectrum_signal_{tag}.png"),
    )
    plot_spectra(
        res_spec,
        f"Residual noise spectrum, 997 Hz @ {level_dbfs} dBFS",
        os.path.join(OUT, f"spectrum_residual_{tag}.png"),
    )
    return results


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    summary = {
        "fs": FS,
        "tone_hz": TONE_HZ,
        "thd_threshold_db": THD_THRESHOLD_DB,
        "lf_band_hz": LF_BAND,
        "hf_band_hz": HF_BAND,
        "full_level_-6dbfs": run_level(-6.0, "full"),
        "low_level_-60dbfs": run_level(-60.0, "lowlevel"),
    }
    shaped_thd = summary["full_level_-6dbfs"]["C_tpdf_shaped"]["thd_db"]
    summary["thd_check"] = {
        "shaped_thd_db": shaped_thd,
        "pass": shaped_thd < THD_THRESHOLD_DB,
    }
    b_lf = summary["low_level_-60dbfs"]["B_tpdf"]["lf_residue_dbfs"]
    c_lf = summary["low_level_-60dbfs"]["C_tpdf_shaped"]["lf_residue_dbfs"]
    summary["lf_residue_check"] = {
        "before_shaping_dbfs": b_lf,
        "after_shaping_dbfs": c_lf,
        "improvement_db": b_lf - c_lf,
        "pass": c_lf < b_lf,
    }
    with open(os.path.join(OUT, "audio_metrics.json"), "w") as fh:
        json.dump(summary, fh, indent=2)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
