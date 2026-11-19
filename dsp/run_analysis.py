"""Run the multiband compressor on a synthetic signal and verify:

1. Gain-reduction curves (soft knee vs hard knee) -> CSV + PNG.
2. Output loudness per segment (dBFS RMS).
3. Overshoot sample positions (output above threshold after compression).
4. Lookahead delays but never advances the transient onset (phase check).
5. No gain boost during silence segments.
"""

import json
import os

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from compressor import MultibandCompressor

FS = 48000
OUT = os.path.join(os.path.dirname(__file__), "..", "artifacts")
os.makedirs(OUT, exist_ok=True)

THRESHOLD_DB = -20.0
RATIO = 4.0
ATTACK_MS = 2.0
RELEASE_MS = 120.0
LOOKAHEAD_MS = 5.0


def make_signal():
    """silence | loud transient burst | quiet tone | silence"""
    rng = np.random.default_rng(42)
    t = lambda n: np.arange(n) / FS
    seg_silence1 = np.zeros(int(0.4 * FS))
    # transient: instant-onset 1 kHz burst at 0 dBFS peak, 200 ms
    n_tr = int(0.2 * FS)
    seg_transient = 0.95 * np.sin(2 * np.pi * 1000 * t(n_tr))
    # steady loud-ish tone with noise, 600 ms
    n_mid = int(0.6 * FS)
    seg_mid = 0.5 * np.sin(2 * np.pi * 440 * t(n_mid)) \
        + 0.05 * rng.standard_normal(n_mid)
    seg_quiet = 0.02 * np.sin(2 * np.pi * 300 * t(int(0.4 * FS)))
    seg_silence2 = np.zeros(int(0.4 * FS))
    parts = [seg_silence1, seg_transient, seg_mid, seg_quiet, seg_silence2]
    bounds = np.cumsum([0] + [len(p) for p in parts])
    x = np.concatenate(parts)
    return x, bounds


def rms_db(x):
    if len(x) == 0:
        return -np.inf
    r = np.sqrt(np.mean(x ** 2))
    return 20 * np.log10(r) if r > 0 else -np.inf


def find_onset(x, thresh=1e-4):
    idx = np.nonzero(np.abs(x) > thresh)[0]
    return int(idx[0]) if len(idx) else None


def ideal_output_envelope(x, threshold_db, ratio):
    """Per-sample target envelope of an ideal instantaneous hard compressor."""
    env = np.abs(x)
    env_db = 20 * np.log10(np.maximum(env, 1e-10))
    over = np.maximum(env_db - threshold_db, 0.0)
    out_db = env_db - over * (1.0 - 1.0 / ratio)
    return 10 ** (out_db / 20)


def run_config(x, bounds, names, knee_db, lookahead_ms, onset_in):
    comp = MultibandCompressor(
        FS, threshold_db=THRESHOLD_DB, ratio=RATIO, knee_db=knee_db,
        attack_ms=ATTACK_MS, release_ms=RELEASE_MS,
        lookahead_ms=lookahead_ms)
    r = comp.process(x)
    y = r["output"]
    la = r["lookahead_samples"]

    # --- onset / phase check: onset may be delayed, never advanced ---
    onset_out = find_onset(y)
    shift = onset_out - onset_in
    # waveform alignment: compare against a unity-gain passthrough of the
    # same filter topology (ratio=1). This isolates the lookahead effect:
    # the compressed transient must sit at the same lag (never earlier)
    # with the same starting polarity.
    ref_comp = MultibandCompressor(
        FS, threshold_db=THRESHOLD_DB, ratio=1.0, knee_db=knee_db,
        attack_ms=ATTACK_MS, release_ms=RELEASE_MS,
        lookahead_ms=lookahead_ms)
    y_ref = ref_comp.process(x)["output"]
    seg_len = int(0.02 * FS)
    ref = y_ref[onset_in:onset_in + seg_len]
    search = y[onset_in:onset_in + seg_len + 4 * max(la, 1)]
    corr = np.correlate(search, ref, mode="valid")
    lag = int(np.argmax(np.abs(corr)))
    polarity = float(np.sign(corr[lag]))
    phase_ok = shift >= 0 and lag == 0 and polarity > 0

    # --- loudness per segment ---
    loud = {name: rms_db(y[bounds[i]:bounds[i + 1]])
            for i, name in enumerate(names)}

    # --- overshoot: cycle peaks of |y| in the 50 ms after the output
    # transient onset that exceed the compressor's own steady-state peak
    # level (median of peaks in the second half of the window) by > 1 dB ---
    win0 = onset_out
    win1 = onset_out + int(0.05 * FS)
    env = np.abs(y[win0:win1])
    peaks = [i for i in range(1, len(env) - 1)
             if env[i] > env[i - 1] and env[i] >= env[i + 1] and env[i] > 1e-3]
    peak_lv = np.array([env[p] for p in peaks])
    steady = np.median(peak_lv[len(peak_lv) // 2:])
    over_peaks = [win0 + peaks[i] for i in range(len(peaks))
                  if 20 * np.log10(peak_lv[i] / steady) > 1.0]
    over_idx = np.array(over_peaks)

    # --- silence check: applied gain must stay <= 1 (no boost) and the
    # gain reduction must fully recover to 0 dB inside silence ---
    sil = {}
    for name in ["silence_1", "silence_2"]:
        i = names.index(name)
        g = r["band_gain"][:, bounds[i]:bounds[i + 1]]
        gr = r["band_gr_db"][:, bounds[i]:bounds[i + 1]]
        sil[name] = {
            "max_applied_gain": float(np.max(g)),
            "max_gr_db": float(np.max(gr)),  # 0 dB = fully recovered
        }
    boost_free = all(v["max_applied_gain"] <= 1.0 + 1e-9
                     and v["max_gr_db"] <= 1e-6 for v in sil.values())

    return {
        "onset_in": onset_in, "onset_out": onset_out, "shift": shift,
        "lookahead_samples": la, "xcorr_lag": lag,
        "onset_polarity": polarity, "phase_ok": bool(phase_ok),
        "loudness_dbfs": {k: (float(v) if np.isfinite(v) else None)
                          for k, v in loud.items()},
        "overshoot_positions": [int(s) for s in over_idx],
        "overshoot_rel_to_onset": [int(s - onset_out) for s in over_idx],
        "silence": sil,
        "silence_boost_free": bool(boost_free),
        "gr": r["band_gr_db"], "y": y,
    }


def main():
    x, bounds = make_signal()
    names = ["silence_1", "transient", "mid_tone", "quiet_tone", "silence_2"]
    onset_in = find_onset(x)
    print(f"input transient onset sample: {onset_in}")

    results = {}
    for knee_name, knee_db in [("soft", 6.0), ("hard", 0.0)]:
        r = run_config(x, bounds, names, knee_db, LOOKAHEAD_MS, onset_in)
        results[knee_name] = r
        print(f"[{knee_name} knee, lookahead {LOOKAHEAD_MS} ms]")
        print(f"  output onset {r['onset_out']}, shift {r['shift']} samples "
              f"(lookahead={r['lookahead_samples']}), xcorr lag "
              f"{r['xcorr_lag']}, polarity {r['onset_polarity']:+.0f}, "
              f"not advanced: {r['phase_ok']}")
        print(f"  loudness dBFS:",
              {k: (round(v, 2) if v is not None else "-inf")
               for k, v in r["loudness_dbfs"].items()})
        over_pairs = list(zip(r["overshoot_positions"][:20],
                              r["overshoot_rel_to_onset"][:20]))
        print(f"  overshoot samples (abs, rel-to-onset): {over_pairs} "
              f"count={len(r['overshoot_positions'])}")
        print(f"  silence: {r['silence']}  no boost: "
              f"{r['silence_boost_free']}")

    # reference: same compressor without lookahead -> expect overshoot
    r0 = run_config(x, bounds, names, 6.0, 0.0, onset_in)
    results["soft_no_lookahead"] = r0
    print(f"[soft knee, lookahead 0 ms] overshoot count="
          f"{len(r0['overshoot_positions'])}, onset shift={r0['shift']}")

    # --- plot gain-reduction curves ---
    t_ms = np.arange(len(x)) / FS * 1000
    fig, axes = plt.subplots(3, 1, figsize=(11, 8), sharex=True)
    for knee_name, color in [("soft", "tab:blue"), ("hard", "tab:red")]:
        gr = results[knee_name]["gr"]
        for b, band_name in enumerate(["low", "mid", "high"]):
            axes[b].plot(t_ms, gr[b], color=color, lw=0.7,
                         label=f"{knee_name} knee" if b == 0 else None,
                         alpha=0.85)
    for b, band_name in enumerate(["low (0-200Hz)", "mid (200-2kHz)",
                                   "high (>2kHz)"]):
        axes[b].set_ylabel(f"{band_name}\nGR (dB)")
        axes[b].set_ylim(-25, 2)
        axes[b].grid(alpha=0.3)
        for bd in bounds[1:-1]:
            axes[b].axvline(bd / FS * 1000, color="k", ls=":", lw=0.6)
    axes[0].legend(loc="lower right")
    axes[-1].set_xlabel("time (ms)")
    fig.suptitle("Multiband compressor gain reduction: soft vs hard knee "
                 f"(thr {THRESHOLD_DB} dB, ratio {RATIO}:1, "
                 f"lookahead {LOOKAHEAD_MS} ms)")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "gain_reduction_curves.png"), dpi=120)

    # --- CSV export ---
    csv_path = os.path.join(OUT, "gain_reduction.csv")
    with open(csv_path, "w") as f:
        f.write("sample,time_ms,soft_low,soft_mid,soft_high,"
                "hard_low,hard_mid,hard_high\n")
        step = 10
        for i in range(0, len(x), step):
            row = [i, i / FS * 1000,
                   *[results[k]["gr"][b][i] for k in ("soft", "hard")
                     for b in range(3)]]
            f.write(",".join(f"{v:.4f}" if isinstance(v, float) else str(v)
                             for v in row) + "\n")

    # --- JSON summary ---
    summary = {k: {kk: vv for kk, vv in v.items() if kk not in ("gr", "y")}
               for k, v in results.items()}
    with open(os.path.join(OUT, "compressor_report.json"), "w") as f:
        json.dump(summary, f, indent=2)
    print("artifacts written to", os.path.abspath(OUT))


if __name__ == "__main__":
    main()
