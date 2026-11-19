"""Film grain experiments: on/off diff, parameter sweeps, reproducibility,
timing, seed-distribution comparison, and bad-frame concealment bounds."""
import copy
import hashlib
import json
import sys
import time

import numpy as np

sys.path.insert(0, "scripts")
from common import load_grain_params, read_yuv
from film_grain import apply_grain_frame

TIERS = ["8", "20", "40"]
OUT = {}


def frame_bytes(frame):
    return b"".join(np.asarray(p, np.uint8).tobytes() for p in frame)


def noise_of(frame_on, frame_off):
    return [np.asarray(a, np.int16) - np.asarray(b, np.int16)
            for a, b in zip(frame_on, frame_off)]


def exp_on_off_diff():
    """Per-frame difference between grain-on and grain-off decodes."""
    rows = []
    for tier in TIERS:
        on = read_yuv(f"dec/l{tier}_on.yuv")
        off = read_yuv(f"dec/l{tier}_off.yuv")
        for i, (fo, ff) in enumerate(zip(on, off)):
            for pl, name in enumerate("YUV"):
                n = noise_of(fo, ff)[pl]
                rows.append(dict(tier=tier, frame=i, plane=name,
                                 mean=float(n.mean()), std=float(n.std()),
                                 mad=float(np.abs(n).mean()),
                                 pmax=int(n.max()), pmin=int(n.min())))
    with open("results/diff_on_off.json", "w") as f:
        json.dump(rows, f)
    summ = {}
    for tier in TIERS:
        r = [x for x in rows if x["tier"] == tier]
        summ[tier] = {
            p: dict(std=float(np.mean([x["std"] for x in r if x["plane"] == p])),
                    mad=float(np.mean([x["mad"] for x in r if x["plane"] == p])))
            for p in "YUV"
        }
    OUT["on_off_diff"] = summ
    print("[on/off] " + json.dumps(summ))


def _scale_points(params, fy=1.0, fuv=1.0):
    p = copy.deepcopy(params)
    p["y_points"] = [(x, max(0, min(255, round(v * fy))))
                     for x, v in p["y_points"]]
    p["uv_points"] = [[(x, max(0, min(255, round(v * fuv))))
                       for x, v in pl] for pl in p["uv_points"]]
    return p


def _set_granularity(params, gss):
    p = copy.deepcopy(params)
    p["grain_scale_shift"] = gss
    return p


def exp_sweeps():
    """Sweep intensity / granularity / chroma-noise; check reproducibility
    (same seed twice -> identical bytes) and measure per-config timing."""
    base_params = load_grain_params("results/params_l20.json")[:12]
    off = read_yuv("dec/l20_off.yuv")[:12]
    configs = {
        "intensity_x0.5": _scale_points(base_params[0], 0.5, 0.5),
        "intensity_x1.0": _scale_points(base_params[0], 1.0, 1.0),
        "intensity_x1.5": _scale_points(base_params[0], 1.5, 1.5),
        "granularity_s0": _set_granularity(base_params[0], 0),
        "granularity_s1": _set_granularity(base_params[0], 1),
        "granularity_s2": _set_granularity(base_params[0], 2),
        "chroma_x0.0": _scale_points(base_params[0], 1.0, 0.0),
        "chroma_x1.0": _scale_points(base_params[0], 1.0, 1.0),
        "chroma_x2.0": _scale_points(base_params[0], 1.0, 2.0),
    }
    results = {}
    for name, p0 in configs.items():
        hashes, times, noise_std, noise_std_u = [], [], [], []
        for rep in range(2):
            h = hashlib.sha256()
            t0 = time.perf_counter()
            for i, fo in enumerate(off):
                p = copy.deepcopy(p0)
                p["seed"] = base_params[i]["seed"]
                syn = apply_grain_frame(fo, p)
                h.update(frame_bytes(syn))
                if rep == 0:
                    nz = noise_of(syn, fo)
                    noise_std.append(float(nz[0].std()))
                    noise_std_u.append(float(nz[1].std()))
            times.append((time.perf_counter() - t0) / len(off) * 1000)
            hashes.append(h.hexdigest())
        results[name] = dict(
            reproducible=hashes[0] == hashes[1],
            sha256=hashes[0][:16],
            ms_per_frame=round(float(np.mean(times)), 2),
            luma_noise_std=round(float(np.mean(noise_std)), 3),
            chroma_noise_std=round(float(np.mean(noise_std_u)), 3),
        )
        print(f"[sweep] {name}: {results[name]}")
    OUT["sweeps"] = results


def exp_tier_timing():
    """Per-tier synthesis timing with the native encoded parameters."""
    res = {}
    for tier in TIERS:
        params = load_grain_params(f"results/params_l{tier}.json")[:12]
        off = read_yuv(f"dec/l{tier}_off.yuv")[:12]
        t0 = time.perf_counter()
        for p, fo in zip(params, off):
            apply_grain_frame(fo, p)
        ms = (time.perf_counter() - t0) / len(off) * 1000
        res[tier] = round(ms, 2)
        print(f"[timing] tier l{tier}: {ms:.2f} ms/frame")
    OUT["tier_timing_ms_per_frame"] = res


def _ks_statistic(a, b):
    a = np.sort(a.ravel())
    b = np.sort(b.ravel())
    allv = np.concatenate([a, b])
    cdf_a = np.searchsorted(a, allv, side="right") / a.size
    cdf_b = np.searchsorted(b, allv, side="right") / b.size
    return float(np.max(np.abs(cdf_a - cdf_b)))


def exp_seed_distribution():
    """Synthesize noise with different seeds on a flat frame; compare the
    statistical distributions (mean/std/skew/kurtosis + pairwise KS)."""
    p0 = load_grain_params("results/params_l20.json")[0]
    flat = (np.full((240, 320), 128, np.uint8),
            np.full((120, 160), 128, np.uint8),
            np.full((120, 160), 128, np.uint8))
    seeds = [7391, 1, 2, 12345, 43690, 65535]
    noises = {}
    for s in seeds:
        p = copy.deepcopy(p0)
        p["seed"] = s
        syn = apply_grain_frame(flat, p)
        noises[s] = noise_of(syn, flat)[0].astype(np.float64)
    stats = {}
    for s, n in noises.items():
        m = n.mean()
        v = n.var()
        stats[s] = dict(mean=round(float(m), 4),
                        std=round(float(n.std()), 4),
                        skew=round(float(((n - m) ** 3).mean() / v ** 1.5), 4),
                        kurt=round(float(((n - m) ** 4).mean() / v ** 2), 4))
    ks = {}
    for i, s1 in enumerate(seeds):
        for s2 in seeds[i + 1:]:
            ks[f"{s1}-{s2}"] = round(_ks_statistic(noises[s1], noises[s2]), 4)
    OUT["seed_distribution"] = dict(stats=stats, ks=ks)
    print("[seeds] " + json.dumps(stats))
    print("[seeds] pairwise KS: " + json.dumps(ks))


def _pearson(a, b):
    a = np.asarray(a, np.float64).ravel()
    b = np.asarray(b, np.float64).ravel()
    a -= a.mean()
    b -= b.mean()
    return float(a @ b / (np.sqrt(a @ a) * np.sqrt(b @ b)))


def exp_concealment():
    """Simulate bad-frame loss: conceal by (A) copying the previous grain-on
    frame, (B) copying the previous grain-off frame and re-synthesizing grain
    with the lost frame's own params/seed. Report waveform (Pearson)
    correlation vs the true decoded frame; lower bound across losses."""
    params = load_grain_params("results/params_l20.json")
    on = read_yuv("dec/l20_on.yuv")
    off = read_yuv("dec/l20_off.yuv")
    lost = [10, 20, 30]
    rows = []
    for k in lost:
        true_y = on[k][0]
        conc_a = on[k - 1][0]
        syn_b = apply_grain_frame(off[k - 1], params[k])
        conc_b = syn_b[0]
        rows.append(dict(
            lost_frame=k,
            r_copy=_pearson(conc_a, true_y),
            r_copy_resynth=_pearson(conc_b, true_y),
            mse_copy=float(((conc_a.astype(np.float64) - true_y) ** 2).mean()),
            mse_copy_resynth=float(
                ((conc_b.astype(np.float64) - true_y) ** 2).mean()),
        ))
    lb_a = min(r["r_copy"] for r in rows)
    lb_b = min(r["r_copy_resynth"] for r in rows)
    OUT["concealment"] = dict(rows=rows, lower_bound_copy=round(lb_a, 4),
                              lower_bound_copy_resynth=round(lb_b, 4))
    for r in rows:
        print(f"[conceal] lost={r['lost_frame']} r_copy={r['r_copy']:.4f} "
              f"r_resynth={r['r_copy_resynth']:.4f} "
              f"mse_copy={r['mse_copy']:.2f} mse_resynth={r['mse_copy_resynth']:.2f}")
    print(f"[conceal] lower bound r: copy={lb_a:.4f} resynth={lb_b:.4f}")


if __name__ == "__main__":
    exp_on_off_diff()
    exp_tier_timing()
    exp_sweeps()
    exp_seed_distribution()
    exp_concealment()
    with open("results/experiments.json", "w") as f:
        json.dump(OUT, f, indent=1)
    print("wrote results/experiments.json")
