"""Scaler comparison: bicubic / lanczos / spline / area."""
import csv
import math
import os

import numpy as np

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "artifacts")
os.makedirs(OUT, exist_ok=True)


# ------------------------------------------------------------- kernels
def kernel_area(x):
    return np.where(np.abs(x) <= 0.5, 1.0, 0.0)


def _cubic(x, a):
    x = np.abs(x)
    x2 = x * x
    x3 = x2 * x
    return np.where(
        x <= 1.0,
        (a + 2.0) * x3 - (a + 3.0) * x2 + 1.0,
        np.where(x < 2.0, a * x3 - 5.0 * a * x2 + 8.0 * a * x - 4.0 * a, 0.0),
    )


def kernel_bicubic(x):  # Catmull-Rom, a = -0.5
    return _cubic(x, -0.5)


def kernel_spline(x):  # cubic B-spline, a = 0 (no negative lobes)
    return _cubic(x, 0.0)


def kernel_lanczos(x):  # 3-lobe Lanczos
    ax = np.abs(x)
    return np.where(ax < 3.0, np.sinc(x) * np.sinc(x / 3.0), 0.0)


KERNELS = {
    "area": (kernel_area, 0.5),
    "bicubic": (kernel_bicubic, 2.0),
    "spline": (kernel_spline, 2.0),
    "lanczos": (kernel_lanczos, 3.0),
}


def _resize_1d(img, out_len, kernel, support, axis):
    """Separable resample; kernel widened by 1/scale when downscaling."""
    in_len = img.shape[axis]
    scale = out_len / in_len
    widen = max(1.0, 1.0 / scale)
    half = support * widen
    out_shape = list(img.shape)
    out_shape[axis] = out_len
    out = np.zeros(out_shape)
    centers = (np.arange(out_len) + 0.5) / scale - 0.5
    for o in range(out_len):
        lo = max(0, int(math.ceil(centers[o] - half)))
        hi = min(in_len - 1, int(math.floor(centers[o] + half)))
        idx = np.arange(lo, hi + 1)
        w = kernel((idx - centers[o]) / widen)
        s = w.sum()
        if s != 0:
            w = w / s
        key = (slice(None),) * axis + (o,)
        src = img[(slice(None),) * axis + (idx,)]
        out[key] = np.tensordot(src, w, axes=([axis], [0]))
    return out


def resize(img, scale, method):
    kernel, support = KERNELS[method]
    h, w = img.shape
    out = _resize_1d(img, max(1, int(round(h * scale))), kernel, support, 0)
    out = _resize_1d(out, max(1, int(round(w * scale))), kernel, support, 1)
    return out


def roundtrip(img, method, factor=0.5):
    down = resize(img, factor, method)
    up = resize(down, 1.0 / factor, method)
    return up[: img.shape[0], : img.shape[1]]


# ------------------------------------------------------------- test images
def make_natural(n=256, seed=7):
    """Synthetic natural-ish image: gradients + texture + shapes."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:n, 0:n].astype(float) / n
    img = 0.5 + 0.3 * np.sin(4 * np.pi * x) * np.cos(3 * np.pi * y)
    img += 0.15 * rng.standard_normal((n, n))
    k = np.ones((5, 5)) / 25.0
    pad = np.pad(img, 2, mode="reflect")
    sm = np.zeros_like(img)
    for i in range(5):
        for j in range(5):
            sm += pad[i : i + n, j : j + n] * k[i, j]
    img = 0.5 * img + 0.5 * sm
    img += ((x - 0.5) ** 2 + (y - 0.5) ** 2 < 0.04) * 0.4
    return np.clip(img, 0.0, 1.0)


def make_step(n=128, lo=0.2, hi=0.8):
    img = np.full((n, n), lo)
    img[:, n // 2 :] = hi
    return img


def make_chirp(n=256):
    """Horizontal chirp, frequency sweeps past Nyquist of half-res grid."""
    x = np.arange(n)
    phase = 2 * np.pi * (0.02 * x + 0.5 * 0.9 * x**2 / n)
    return (0.5 + 0.5 * np.cos(phase))[None, :].repeat(n, axis=0)


def make_fine_lines(n=256, period=8, width=1):
    """High-contrast 1px vertical lines on black background."""
    img = np.zeros((n, n))
    img[:, ::period] = 1.0
    if width > 1:
        img = np.clip(np.convolve(img[0], np.ones(width), "same")[None, :] + img, 0, 1)
    return img


# ------------------------------------------------------------- metrics
def ssim(a, b, win=7):
    """Standard SSIM with uniform window, C1/C2 from 0-1 range."""
    c1, c2 = 0.01**2, 0.03**2
    k = np.ones(win) / win

    def filt(im):
        out = np.apply_along_axis(lambda v: np.convolve(v, k, "same"), 0, im)
        return np.apply_along_axis(lambda v: np.convolve(v, k, "same"), 1, out)

    mu_a, mu_b = filt(a), filt(b)
    va = filt(a * a) - mu_a**2
    vb = filt(b * b) - mu_b**2
    cov = filt(a * b) - mu_a * mu_b
    m = ((2 * mu_a * mu_b + c1) * (2 * cov + c2)) / (
        (mu_a**2 + mu_b**2 + c1) * (va + vb + c2)
    )
    edge = win // 2
    return float(m[edge:-edge, edge:-edge].mean())


def edge_overshoot(orig, rt, lo=0.2, hi=0.8):
    """Max over/undershoot past step levels, as % of step amplitude."""
    amp = hi - lo
    over = max(0.0, float(rt.max()) - hi) / amp * 100.0
    under = max(0.0, lo - float(rt.min())) / amp * 100.0
    return over, under


def aliasing_energy(down, factor=0.5):
    """Std-dev of downscaled chirp in the beyond-Nyquist zone (ideal = 0)."""
    n = down.shape[1]
    x_out = np.arange(n)
    x_orig = x_out / factor  # corresponding position in original image
    n_orig = n / factor
    orig_freq = 0.02 + 0.9 * x_orig / n_orig  # cycles/px in original domain
    mask = orig_freq > 0.5 * factor  # beyond Nyquist of the output grid
    row = down[down.shape[0] // 2]
    return float(row[mask].std())


def ringing_width(profile, center, baseline, peak, tol=0.02):
    """Width (px) of out-of-range ringing around a line.

    Counts pixels that dip below `baseline - tol*amp` (negative lobes)
    or rise above `peak + tol*amp` (overshoot) -- i.e. excursions that a
    monotone, non-ringing filter can never produce.
    """
    amp = peak - baseline
    if amp <= 0:
        return 0
    bad = np.where((profile < baseline - tol * amp)
                   | (profile > peak + tol * amp))[0]
    if bad.size == 0:
        return 0
    return int(bad.max() - bad.min() + 1)


def ringing_stats_fine_lines(img, method, factor=0.5, period=8):
    """Ringing width per line on the high-contrast fine-line chart."""
    rt = roundtrip(img, method, factor)
    row = rt[rt.shape[0] // 2]
    widths = []
    unders = []
    for c in range(0, img.shape[1], period):
        lo, hi = max(0, c - period), min(img.shape[1], c + period + 1)
        seg = row[lo:hi]
        widths.append(ringing_width(seg, c - lo, baseline=0.0, peak=1.0))
        unders.append(max(0.0, -float(seg.min())))
    widths = np.array(widths, dtype=float)
    return {
        "mean": float(widths.mean()),
        "max": float(widths.max()),
        "p95": float(np.percentile(widths, 95)),
        "undershoot": float(np.max(unders)),
    }


def step_ringing_width(rt, lo=0.2, hi=0.8, tol=0.02, max_r=24):
    """Width (px) of out-of-range ringing around the step edge."""
    row = rt[rt.shape[0] // 2]
    edge = rt.shape[1] // 2
    amp = hi - lo
    bad = np.where((row < lo - tol * amp) | (row > hi + tol * amp))[0]
    bad = bad[np.abs(bad - edge) <= max_r]
    if bad.size == 0:
        return 0
    return int(bad.max() - bad.min() + 1)


def fine_line_contrast(img, method, factor=0.5, period=8):
    """Peak line intensity after roundtrip (1.0 = fully preserved)."""
    rt = roundtrip(img, method, factor)
    row = rt[rt.shape[0] // 2]
    peaks = [row[c] for c in range(0, img.shape[1], period)]
    return float(np.mean(peaks))


# ------------------------------------------------------------- ducking
def ducking_gain_curve(depth_db, sr=1000.0, dur=2.0, attack=0.005, release=0.2):
    """Feedforward ducker: gain drops by `depth_db` while the key is hot.

    Key signal: loud during t in [0.5, 1.2)s, silent otherwise.
    Returns (t, gain_db, measured_depth_db, release_time_s).
    """
    n = int(dur * sr)
    t = np.arange(n) / sr
    key = ((t >= 0.5) & (t < 1.2)).astype(float)
    target = -depth_db * key
    a_att = 1.0 - math.exp(-1.0 / (attack * sr))
    a_rel = 1.0 - math.exp(-1.0 / (release * sr))
    gain = np.zeros(n)
    g = 0.0
    for i in range(n):
        coef = a_att if target[i] < g else a_rel
        g += coef * (target[i] - g)
        gain[i] = g
    measured_depth = -float(gain.min())
    end = int(1.2 * sr)
    floor = gain[end - 1]  # most negative gain at key release
    # recovery lag: time until gain has moved 1% of the way back toward 0
    moved = np.where(gain[end:] >= floor * 0.99)[0]
    lag = float(moved[0] / sr) if moved.size else float("inf")
    # release tau: time to recover 63.2% of the way back to 0 dB
    tau63 = np.where(gain[end:] >= floor * (1.0 - 0.632))[0]
    tau = float(tau63[0] / sr) if tau63.size else float("inf")
    return t, gain, measured_depth, lag, tau


def run_ducking(depths=(6.0, 12.0, 18.0, 24.0), release=0.2):
    rows = []
    for d in depths:
        t, g, md, lag, tau = ducking_gain_curve(d, release=release)
        rows.append((d, md, lag, tau))
        path = os.path.join(OUT, f"ducking_gain_{int(d)}dB.csv")
        with open(path, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["time_s", "gain_db"])
            step = max(1, len(t) // 400)
            for ti, gi in zip(t[::step], g[::step]):
                w.writerow([f"{ti:.4f}", f"{gi:.3f}"])
    return rows


# ------------------------------------------------------------- main
def main():
    methods = list(KERNELS)
    natural = make_natural()
    step_img = make_step()
    chirp = make_chirp()
    lines = make_fine_lines()

    print("=" * 78)
    print("缩放器对比: 0.5x 下采样 + 2x 上采样往返")
    print("=" * 78)
    hdr = (f"{'方法':<9}{'SSIM':>8}{'过冲%':>8}{'下冲%':>8}{'混叠std':>10}"
           f"{'边缘振铃px':>11}{'细线峰值':>9}")
    print(hdr)
    print("-" * 78)
    results = {}
    for m in methods:
        rt_nat = roundtrip(natural, m)
        rt_step = roundtrip(step_img, m)
        down_chirp = resize(chirp, 0.5, m)
        over, under = edge_overshoot(step_img, rt_step)
        results[m] = {
            "ssim": ssim(natural, rt_nat),
            "over": over,
            "under": under,
            "alias": aliasing_energy(down_chirp),
            "step_ring": step_ringing_width(rt_step),
            "line_peak": fine_line_contrast(lines, m),
            "ring": ringing_stats_fine_lines(lines, m),
        }
        r = results[m]
        print(f"{m:<9}{r['ssim']:>8.4f}{r['over']:>8.2f}{r['under']:>8.2f}"
              f"{r['alias']:>10.4f}{r['step_ring']:>11d}{r['line_peak']:>9.3f}")

    print()
    print("高对比细线图振铃宽度统计 (px, 容差 2% 幅度)")
    print("-" * 78)
    print(f"{'方法':<9}{'平均宽度':>10}{'P95':>8}{'最大宽度':>10}{'最大下冲':>10}")
    for m in methods:
        rg = results[m]["ring"]
        print(f"{m:<9}{rg['mean']:>10.2f}{rg['p95']:>8.1f}{rg['max']:>10.1f}"
              f"{rg['undershoot']:>10.3f}")

    best = min(methods, key=lambda m: results[m]["ring"]["mean"])
    print()
    print(f"振铃最稳定(平均宽度最小): {best}")
    erased = [m for m in methods if results[m]["line_peak"] < 0.3]
    print("细线保留: " + ("全部方法细线峰值 >= 0.3, 未被抹掉"
                          if not erased else f"警告: {erased} 细线接近消失"))

    print()
    print("闪避(ducking)深度与恢复 (release=200ms)")
    print("-" * 78)
    print(f"{'设定深度dB':>10}{'实测深度dB':>12}{'恢复滞后ms':>12}{'实测τms':>10}")
    for d, md, lag, tau in run_ducking():
        print(f"{d:>10.1f}{md:>12.2f}{lag * 1000:>12.1f}{tau * 1000:>10.1f}")
    print(f"增益曲线已写入 {OUT}/ducking_gain_*dB.csv")

    with open(os.path.join(OUT, "scaler_metrics.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["method", "ssim", "overshoot_pct", "undershoot_pct",
                    "aliasing_std", "edge_ring_px", "line_peak",
                    "ring_w_mean", "ring_w_p95", "ring_w_max",
                    "ring_undershoot"])
        for m in methods:
            r = results[m]
            w.writerow([m, f"{r['ssim']:.4f}", f"{r['over']:.2f}",
                        f"{r['under']:.2f}", f"{r['alias']:.4f}",
                        r["step_ring"], f"{r['line_peak']:.3f}",
                        f"{r['ring']['mean']:.2f}", f"{r['ring']['p95']:.1f}",
                        f"{r['ring']['max']:.1f}", f"{r['ring']['undershoot']:.3f}"])
    print(f"指标表已写入 {OUT}/scaler_metrics.csv")


if __name__ == "__main__":
    main()
