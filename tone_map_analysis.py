"""PQ/HLG -> SDR tone mapping analysis (Reinhard vs ACES).

Generates synthetic HDR test scenes (no input footage in workspace),
applies Reinhard and ACES tone mapping, and reports:
  - before/after histograms (PNG)
  - hue angle shift (max, degrees)
  - highlight retention
  - dark-region pixel ratios (crush check)
"""
import json
import os

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "artifacts")
os.makedirs(OUT, exist_ok=True)

BT2020_TO_BT709 = np.array(
    [
        [1.6604910, -0.5876411, -0.0728499],
        [-0.1245505, 1.1328999, -0.0083494],
        [-0.0181508, -0.1005789, 1.1187297],
    ]
)

PQ_M1 = 2610 / 16384
PQ_M2 = 2523 / 32
PQ_C1 = 3424 / 4096
PQ_C2 = 2413 / 128
PQ_C3 = 2392 / 128
PQ_NITS_MAX = 10000.0
SDR_WHITE_NITS = 203.0


def pq_decode(e):
    """PQ EOTF: non-linear signal [0,1] -> linear absolute nits."""
    e = np.clip(e, 0.0, 1.0)
    ep = e ** (1.0 / PQ_M2)
    num = np.maximum(ep - PQ_C1, 0.0)
    den = PQ_C2 - PQ_C3 * ep
    return (num / den) ** (1.0 / PQ_M1) * PQ_NITS_MAX


def hlg_decode(e, peak_nits=1000.0):
    """HLG inverse OETF + OOTF (system gamma 1.2) -> linear absolute nits."""
    e = np.clip(e, 0.0, 1.0)
    a, b, c = 0.17883277, 0.28466892, 0.55991073
    inv = np.where(
        e <= 0.5,
        (e ** 2) / 3.0,
        (np.exp((e - c) / a) + b) / 12.0,
    )
    lum = inv @ np.array([0.2627, 0.6780, 0.0593])
    gain = peak_nits * np.power(np.maximum(lum, 1e-10), 0.2)
    return inv * gain[..., None]


def make_test_scene(w=640, h=360):
    """Linear BT.2020 scene in nits: color patches, gradients, shadow detail."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    scene = np.zeros((h, w, 3))

    t = xx / (w - 1)
    ramp = 10 ** (np.log10(0.001) + t * (np.log10(4000.0) - np.log10(0.001)))
    scene[:] = ramp[..., None] * np.array([0.9, 0.9, 0.9])

    patch_colors = [
        (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0),
        (1.0, 1.0, 0.0), (0.0, 1.0, 1.0), (1.0, 0.0, 1.0),
    ]
    band_h = h // 4
    pw = w // len(patch_colors)
    for i, c in enumerate(patch_colors):
        scene[0:band_h, i * pw:(i + 1) * pw] = np.array(c) * 800.0

    band0 = h - h // 5
    steps = 8
    sw = w // steps
    for i in range(steps):
        lvl = 0.001 * (200.0 ** (i / (steps - 1)))
        scene[band0:h, i * sw:(i + 1) * sw] = lvl

    cx, cy, r = w // 2, h // 2, h // 6
    mask = (xx - cx) ** 2 + (yy - cy) ** 2 < r ** 2
    scene[mask] = np.array([1.0, 0.95, 0.9]) * 5000.0

    return scene


def to_bt709_linear(rgb2020_nits):
    """BT.2020 linear nits -> BT.709 linear, normalized to SDR white = 1."""
    rgb = rgb2020_nits @ BT2020_TO_BT709.T
    return rgb / SDR_WHITE_NITS


def luminance709(rgb):
    return rgb @ np.array([0.2126, 0.7152, 0.0722])


def tonemap_reinhard(rgb):
    """Luminance-domain Reinhard: L' = L / (1 + L), chroma preserved."""
    lum = luminance709(rgb)
    mapped = lum / (1.0 + lum)
    scale = mapped / np.maximum(lum, 1e-10)
    return np.clip(rgb * scale[..., None], 0.0, 1.0)


def _aces_fit(x):
    """Stephen Hill ACES fit (RRT+ODT, sRGB 100nit target)."""
    a = 2.51
    b = 0.03
    c = 2.43
    d = 0.59
    e = 0.14
    return np.clip((x * (a * x + b)) / (x * (c * x + d) + e), 0.0, 1.0)


ACES_INPUT = np.array(
    [
        [0.59719, 0.35458, 0.04823],
        [0.07600, 0.90834, 0.01566],
        [0.02840, 0.13383, 0.83777],
    ]
)
ACES_OUTPUT = np.array(
    [
        [1.60475, -0.53108, -0.07367],
        [-0.10208, 1.10813, -0.00605],
        [-0.00327, -0.07276, 1.07602],
    ]
)


def tonemap_aces(rgb):
    """ACES filmic tone mapping (Hill fit), exposure-scaled."""
    x = rgb * 0.6  # ACESproxy-style exposure offset used by the fit
    x = x @ ACES_INPUT.T
    x = _aces_fit(x)
    x = x @ ACES_OUTPUT.T
    return np.clip(x, 0.0, 1.0)


def srgb_encode(rgb):
    rgb = np.clip(rgb, 0.0, 1.0)
    return np.where(
        rgb <= 0.0031308,
        12.92 * rgb,
        1.055 * np.power(rgb, 1.0 / 2.4) - 0.055,
    )


def hue_angles(rgb):
    """Hue angle in degrees from RGB (HSV-style), NaN for near-achromatic."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    cmax = rgb.max(axis=-1)
    cmin = rgb.min(axis=-1)
    delta = cmax - cmin
    hue = np.zeros_like(cmax)
    m = delta > 1e-9
    rm = m & (cmax == r)
    gm = m & (cmax == g) & (cmax != r)
    bm = m & (cmax == b) & (cmax != r) & (cmax != g)
    hue[rm] = (60.0 * ((g - b) / delta))[rm] % 360.0
    hue[gm] = (60.0 * ((b - r) / delta) + 120.0)[gm]
    hue[bm] = (60.0 * ((r - g) / delta) + 240.0)[bm]
    hue[~m] = np.nan
    return hue


def max_hue_shift(rgb_before, rgb_after):
    """Max circular hue-angle shift (deg) over pixels with real chroma."""
    h0 = hue_angles(rgb_before)
    h1 = hue_angles(rgb_after)
    valid = ~np.isnan(h0) & ~np.isnan(h1)
    # Ignore near-achromatic pixels where hue is numerically unstable
    sat0 = rgb_before.max(-1) - rgb_before.min(-1)
    valid &= sat0 > 0.02 * np.maximum(rgb_before.max(-1), 1e-9)
    if not valid.any():
        return 0.0
    d = np.abs(h1[valid] - h0[valid]) % 360.0
    d = np.minimum(d, 360.0 - d)
    return float(d.max())


def plot_histograms(tag, lin_before, sdr_after):
    """Luminance histogram before (log-nits) and after (SDR 0-1)."""
    lum_b = luminance709(lin_before).ravel()
    lum_a = luminance709(sdr_after).ravel()
    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    pos = lum_b[lum_b > 0]
    axes[0].hist(
        np.log10(pos), bins=120, color="steelblue"
    )
    axes[0].set_title(f"{tag} BEFORE (log10 nits, SDR white=1)")
    axes[0].set_xlabel("log10 luminance")
    axes[1].hist(lum_a, bins=120, color="darkorange")
    axes[1].set_title(f"{tag} AFTER (SDR 0-1)")
    axes[1].set_xlabel("luminance")
    for ax in axes:
        ax.set_ylabel("pixels")
    fig.tight_layout()
    path = os.path.join(OUT, f"hist_{tag}.png")
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path


def dark_region_stats(lin_before, sdr_after):
    """Pixel ratios in dark zones; pure-black crush check."""
    lb = luminance709(lin_before)
    la = luminance709(sdr_after)
    n = lb.size
    stats = {
        "before_ratio_lum<0.018": float((lb < 0.018).sum() / n),
        "after_ratio_lum<0.018": float((la < 0.018).sum() / n),
        "before_ratio_pure_black": float((lb <= 0.0).sum() / n),
        "after_ratio_pure_black": float((la <= 0.0).sum() / n),
    }
    # Crush check: pixels that had visible shadow detail before
    # (0.0005..0.018 of SDR white) must not collapse to pure black after.
    shadow_detail = (lb > 0.0005) & (lb < 0.018)
    crushed = (la <= 0.0) & shadow_detail
    stats["shadow_detail_pixels"] = int(shadow_detail.sum())
    stats["crushed_to_black"] = int(crushed.sum())
    stats["shadow_crushed"] = bool(crushed.sum() > 0)
    return stats


def highlight_retention(lin_before, sdr_after):
    """Fraction of above-SDR-white pixels that stay below clip (retained),
    plus how much of the top range is used after mapping."""
    lb = luminance709(lin_before)
    la = luminance709(sdr_after)
    hi = lb > 1.0
    if hi.sum() == 0:
        return {"highlight_pixels": 0}
    retained = (la[hi] < 0.999).sum() / hi.sum()
    return {
        "highlight_pixels": int(hi.sum()),
        "ratio_retained_below_clip": float(retained),
        "ratio_clipped_white": float(1.0 - retained),
        "after_max_lum": float(la.max()),
    }


def run_case(name, lin_nits_2020):
    lin709 = to_bt709_linear(lin_nits_2020)
    results = {}
    for mapper_name, fn in [("reinhard", tonemap_reinhard), ("aces", tonemap_aces)]:
        tag = f"{name}_{mapper_name}"
        sdr = fn(lin709)
        sdr_enc = srgb_encode(sdr)
        plt.imsave(os.path.join(OUT, f"sdr_{tag}.png"), sdr_enc)
        results[tag] = {
            "histogram": plot_histograms(tag, lin709, sdr),
            "max_hue_shift_deg": max_hue_shift(srgb_encode(np.clip(lin709, 0, 1)), sdr_enc),
            "dark": dark_region_stats(lin709, sdr),
            "highlight": highlight_retention(lin709, sdr),
        }
    return results


def main():
    scene = make_test_scene()
    peak = scene.max()

    # Encode scene as PQ and HLG signals, then decode (round-trip inputs).
    pq_signal = np.clip(
        ((PQ_C1 + PQ_C2 * (scene / PQ_NITS_MAX) ** PQ_M1)
         / (1 + PQ_C3 * (scene / PQ_NITS_MAX) ** PQ_M1)) ** PQ_M2,
        0.0, 1.0,
    )
    lin_pq = pq_decode(pq_signal)

    # HLG: normalize to 1000 nit peak, apply inverse OOTF then OETF
    peak_hlg = 1000.0
    rel = scene / peak_hlg
    lum = rel @ np.array([0.2627, 0.6780, 0.0593])
    inv_ootf = rel * np.power(np.maximum(lum, 1e-10), -0.2)[..., None]
    a, b, c = 0.17883277, 0.28466892, 0.55991073
    v = np.clip(12.0 * inv_ootf, 0.0, None)
    hlg_signal = np.where(
        inv_ootf <= 1.0 / 12.0,
        np.sqrt(3.0 * np.clip(inv_ootf, 0, None)),
        a * np.log(np.clip(v - b, 1e-10, None)) + c,
    )
    lin_hlg = hlg_decode(np.clip(hlg_signal, 0, 1), peak_hlg)

    report = {"scene_peak_nits": float(peak)}
    report.update(run_case("pq", lin_pq))
    report.update(run_case("hlg", lin_hlg))

    with open(os.path.join(OUT, "report.json"), "w") as f:
        json.dump(report, f, indent=2)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
