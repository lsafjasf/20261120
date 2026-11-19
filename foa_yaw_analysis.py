"""FOA ACN/SN3D encoding, yaw rotation, FuMa conversion and validations.

1. ACN/SN3D plane-wave encoding
2. Yaw rotation matrix + energy conservation check
3. ACN/SN3D <-> FuMa conversion and round-trip error
4. PSNR / SSIM / MS-SSIM comparison on noisy frames
5. Low-band spatial aliasing check (element spacing vs half wavelength)
"""

import json
import os

import numpy as np

ARTIFACTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "artifacts")
os.makedirs(ARTIFACTS, exist_ok=True)

C_SOUND = 343.0  # m/s


# ---------------------------------------------------------------------------
# 1. ACN/SN3D encoding
# ---------------------------------------------------------------------------
def encode_acn_sn3d(azimuth, elevation, n_samples, fs=48000.0, freq=440.0):
    """Plane-wave FOA encoding, ACN channel order [W, Y, Z, X], SN3D norm.

    First-order real SN3D harmonics:
        W = 1
        Y = sin(az) * cos(el)
        Z = sin(el)
        X = cos(az) * cos(el)
    """
    t = np.arange(n_samples)
    src = np.sin(2.0 * np.pi * freq * t / fs)
    w = np.ones(n_samples)
    y = np.sin(azimuth) * np.cos(elevation) * np.ones(n_samples)
    z = np.sin(elevation) * np.ones(n_samples)
    x = np.cos(azimuth) * np.cos(elevation) * np.ones(n_samples)
    gains = np.stack([w, y, z, x], axis=0)  # (4, N)
    return gains * src[np.newaxis, :]


def yaw_rotation_matrix(alpha):
    """Yaw rotation matrix (scene rotated by alpha about z), ACN order [W,Y,Z,X].

    X' =  cos(a) X - sin(a) Y
    Y' =  sin(a) X + cos(a) Y
    W, Z unchanged. Matrix is orthogonal => total energy preserved.
    """
    c, s = np.cos(alpha), np.sin(alpha)
    return np.array([
        [1.0, 0.0, 0.0, 0.0],   # W'
        [0.0,   c, 0.0,   s],   # Y'
        [0.0, 0.0, 1.0, 0.0],   # Z'
        [0.0,  -s, 0.0,   c],   # X'
    ])


def channel_energies(bfmt):
    """Per-channel energy (sum of squares over time)."""
    return np.sum(bfmt ** 2, axis=1)


# ---------------------------------------------------------------------------
# 2. FuMa conversion
# ---------------------------------------------------------------------------
def acn_sn3d_to_fuma_matrix():
    """ACN/SN3D [W,Y,Z,X] -> FuMa [W,X,Y,Z] (maxN norm).

    At first order SN3D and maxN differ only on W: W_fuma = W_acn / sqrt(2).
    """
    return np.array([
        [1.0 / np.sqrt(2.0), 0.0, 0.0, 0.0],  # W
        [0.0, 0.0, 0.0, 1.0],                 # X <- ACN X
        [0.0, 1.0, 0.0, 0.0],                 # Y <- ACN Y
        [0.0, 0.0, 1.0, 0.0],                 # Z <- ACN Z
    ])


def fuma_to_acn_sn3d_matrix():
    return np.linalg.inv(acn_sn3d_to_fuma_matrix())


# ---------------------------------------------------------------------------
# 3. Image quality metrics: PSNR / SSIM / MS-SSIM
# ---------------------------------------------------------------------------
def _gaussian_kernel(size=11, sigma=1.5):
    x = np.arange(size) - (size - 1) / 2.0
    g = np.exp(-(x ** 2) / (2.0 * sigma ** 2))
    k = g / g.sum()
    return np.outer(k, k)


def _filter2d(img, kernel):
    """Zero-padded 2D correlation with odd-sized kernel (valid for SSIM use)."""
    pad = kernel.shape[0] // 2
    padded = np.pad(img, pad, mode="symmetric")
    out = np.zeros_like(img)
    for i in range(kernel.shape[0]):
        for j in range(kernel.shape[1]):
            out += kernel[i, j] * padded[i:i + img.shape[0], j:j + img.shape[1]]
    return out


def ssim_map(img1, img2, data_range=1.0):
    k1, k2 = 0.01, 0.03
    c1 = (k1 * data_range) ** 2
    c2 = (k2 * data_range) ** 2
    kernel = _gaussian_kernel()
    mu1 = _filter2d(img1, kernel)
    mu2 = _filter2d(img2, kernel)
    mu1_sq, mu2_sq, mu1_mu2 = mu1 * mu1, mu2 * mu2, mu1 * mu2
    sigma1_sq = _filter2d(img1 * img1, kernel) - mu1_sq
    sigma2_sq = _filter2d(img2 * img2, kernel) - mu2_sq
    sigma12 = _filter2d(img1 * img2, kernel) - mu1_mu2
    cs = c2 / 2.0
    contrast_struct = (2.0 * sigma12 + c2) / (sigma1_sq + sigma2_sq + c2)
    ssim = ((2.0 * mu1_mu2 + c1) / (mu1_sq + mu2_sq + c1)) * contrast_struct
    return ssim, contrast_struct


def ssim(img1, img2, data_range=1.0):
    return float(np.mean(ssim_map(img1, img2, data_range)[0]))


def ms_ssim(img1, img2, data_range=1.0, weights=None):
    """MS-SSIM with 5 scales (Wang et al. 2003 weights)."""
    if weights is None:
        weights = np.array([0.0448, 0.2856, 0.3001, 0.2363, 0.1333])
    mcs = []
    a, b = img1, img2
    for i in range(len(weights)):
        s, cs = ssim_map(a, b, data_range)
        mcs.append(float(np.mean(cs)))
        if i == len(weights) - 1:
            mssim_last = float(np.mean(s))
            break
        # downsample by 2 with simple 2x2 average
        a = (a[0::2, 0::2] + a[1::2, 0::2] + a[0::2, 1::2] + a[1::2, 1::2]) / 4.0
        b = (b[0::2, 0::2] + b[1::2, 0::2] + b[0::2, 1::2] + b[1::2, 1::2]) / 4.0
    mcs = np.clip(np.array(mcs[:-1]), 0.0, None)
    return float(np.prod(mcs ** weights[:-1]) * max(mssim_last, 0.0) ** weights[-1])


def psnr(img1, img2, data_range=1.0):
    mse = float(np.mean((img1 - img2) ** 2))
    if mse == 0.0:
        return float("inf")
    return 10.0 * np.log10(data_range ** 2 / mse)


def make_test_frame(size=256):
    """Synthetic clean frame: smooth gradient + disk + bars."""
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float64) / size
    img = 0.5 * xx + 0.3 * yy
    r = np.sqrt((xx - 0.5) ** 2 + (yy - 0.5) ** 2)
    img[r < 0.2] = 0.9
    img[::32, :] = np.clip(img[::32, :] + 0.15, 0, 1)
    return np.clip(img, 0.0, 1.0)


# ---------------------------------------------------------------------------
# 4. Spatial aliasing check (uniform linear array, delay-and-sum beam)
# ---------------------------------------------------------------------------
def beampattern_ula(n_mics, spacing, freq, steer_angle=0.0, n_angles=721):
    """Delay-and-sum beampattern magnitude over look angles [-90, 90] deg."""
    angles = np.linspace(-np.pi / 2, np.pi / 2, n_angles)
    m = np.arange(n_mics)
    k = 2.0 * np.pi * freq / C_SOUND
    # steering delays for broadside target
    steer = np.exp(-1j * k * m[:, None] * spacing * np.sin(steer_angle))
    resp = np.exp(1j * k * m[:, None] * spacing * np.sin(angles)[None, :])
    pattern = np.abs(np.sum(steer * resp, axis=0)) / n_mics
    return angles, pattern


def check_spatial_aliasing(spacing, low_band_hz, n_mics=8):
    """Confirm no grating lobes in the low band (spacing <= lambda/2)."""
    results = {}
    for f in low_band_hz:
        wavelength = C_SOUND / f
        ratio = spacing / wavelength
        _, pattern = beampattern_ula(n_mics, spacing, f)
        # grating lobe: secondary peak close to main lobe height away from 0 deg
        main = pattern[np.argmax(np.abs(np.degrees(np.linspace(
            -np.pi / 2, np.pi / 2, len(pattern)))) < 5.0)].max()
        sidelobe_mask = np.abs(np.degrees(np.linspace(
            -np.pi / 2, np.pi / 2, len(pattern)))) > 30.0
        max_offaxis = pattern[sidelobe_mask].max()
        results[f] = {
            "wavelength_m": wavelength,
            "spacing_over_lambda": ratio,
            "aliasing_free": ratio <= 0.5,
            "main_lobe": float(main),
            "max_offaxis_level": float(max_offaxis),
        }
    return results


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    report = {}
    n_samples = 4800
    az, el = np.deg2rad(30.0), np.deg2rad(10.0)
    alpha = np.deg2rad(45.0)  # yaw angle

    # --- 1. encode ---
    bfmt = encode_acn_sn3d(az, el, n_samples)
    e_before = channel_energies(bfmt)

    # --- 2. yaw rotation ---
    R = yaw_rotation_matrix(alpha)
    bfmt_rot = R @ bfmt
    e_after = channel_energies(bfmt_rot)

    total_before = float(e_before.sum())
    total_after = float(e_after.sum())
    orth_err = float(np.max(np.abs(R @ R.T - np.eye(4))))
    report["yaw_rotation"] = {
        "alpha_deg": float(np.degrees(alpha)),
        "rotation_matrix_ACN_WYZX": R.tolist(),
        "orthogonality_error": orth_err,
        "energy_before_per_channel_WYZX": e_before.tolist(),
        "energy_after_per_channel_WYZX": e_after.tolist(),
        "total_energy_before": total_before,
        "total_energy_after": total_after,
        "total_energy_relative_error": abs(total_after - total_before) / total_before,
        "energy_conserved": bool(np.isclose(total_before, total_after, rtol=1e-12)),
    }

    print("=== Yaw rotation (ACN/SN3D, order [W,Y,Z,X]) ===")
    print(f"yaw angle: {np.degrees(alpha):.1f} deg")
    print("rotation matrix R:")
    for row, name in zip(R, ["W'", "Y'", "Z'", "X'"]):
        print(f"  {name}: " + "  ".join(f"{v:+.6f}" for v in row))
    print(f"orthogonality ||R R^T - I||_max = {orth_err:.3e}")
    print(f"energy per channel before [W,Y,Z,X]: {e_before}")
    print(f"energy per channel after  [W,Y,Z,X]: {e_after}")
    print(f"total energy before = {total_before:.6f}")
    print(f"total energy after  = {total_after:.6f}")
    print(f"sum of per-channel energies conserved: "
          f"{np.isclose(total_before, total_after, rtol=1e-12)}")

    # --- 3. FuMa conversion ---
    C_fwd = acn_sn3d_to_fuma_matrix()
    C_inv = fuma_to_acn_sn3d_matrix()
    fuma = C_fwd @ bfmt_rot
    bfmt_roundtrip = C_inv @ fuma
    rt_err = float(np.max(np.abs(bfmt_roundtrip - bfmt_rot)))
    report["fuma_conversion"] = {
        "acn_sn3d_to_fuma_matrix": C_fwd.tolist(),
        "fuma_to_acn_sn3d_matrix": C_inv.tolist(),
        "roundtrip_max_abs_error": rt_err,
        "note": "FuMa order [W,X,Y,Z], maxN norm; only W differs (1/sqrt(2)).",
    }
    print("\n=== FuMa conversion (ACN/SN3D -> FuMa [W,X,Y,Z]) ===")
    print("conversion matrix C (FuMa = C @ ACN):")
    for row, name in zip(C_fwd, ["W", "X", "Y", "Z"]):
        print(f"  {name}: " + "  ".join(f"{v:+.6f}" for v in row))
    print(f"round-trip max abs error (ACN->FuMa->ACN): {rt_err:.3e}")

    # --- 4. metrics on noisy frames ---
    print("\n=== Metrics on noisy frames (clean vs noisy) ===")
    clean = make_test_frame()
    rng = np.random.default_rng(42)
    metric_rows = []
    for sigma in [0.05, 0.15, 0.30]:
        noisy = np.clip(clean + rng.normal(0.0, sigma, clean.shape), 0.0, 1.0)
        row = {
            "noise_sigma": sigma,
            "psnr_db": psnr(noisy, clean),
            "ssim": ssim(noisy, clean),
            "ms_ssim": ms_ssim(noisy, clean),
        }
        metric_rows.append(row)
        print(f"sigma={sigma:.2f}  PSNR={row['psnr_db']:6.2f} dB  "
              f"SSIM={row['ssim']:.4f}  MS-SSIM={row['ms_ssim']:.4f}")
    high = metric_rows[-1]
    drop_ssim = 1.0 - high["ssim"]
    drop_ms = 1.0 - high["ms_ssim"]
    report["noise_metrics"] = {
        "rows": metric_rows,
        "high_noise_frame": high,
        "quality_drop_ssim": drop_ssim,
        "quality_drop_ms_ssim": drop_ms,
        "ms_ssim_less_sensitive": bool(drop_ms < drop_ssim),
    }
    print(f"high-noise frame (sigma=0.30): SSIM drop={drop_ssim:.4f}, "
          f"MS-SSIM drop={drop_ms:.4f} -> "
          f"MS-SSIM less sensitive: {drop_ms < drop_ssim}")

    # --- 5. spatial aliasing ---
    print("\n=== Spatial aliasing check (ULA, d=0.04 m) ===")
    spacing = 0.04
    low_band = [250.0, 500.0, 1000.0, 2000.0, 4000.0]
    alias = check_spatial_aliasing(spacing, low_band)
    f_alias = C_SOUND / (2.0 * spacing)
    for f, r in alias.items():
        print(f"f={f:7.1f} Hz  d/lambda={r['spacing_over_lambda']:.3f}  "
              f"aliasing_free={r['aliasing_free']}  "
              f"max off-axis level={r['max_offaxis_level']:.3f}")
    low_band_ok = all(r["aliasing_free"] for f, r in alias.items() if f <= f_alias)
    report["spatial_aliasing"] = {
        "array_spacing_m": spacing,
        "aliasing_frequency_hz": f_alias,
        "per_frequency": {str(f): r for f, r in alias.items()},
        "low_band_aliasing_free": bool(low_band_ok),
    }
    print(f"aliasing (Nyquist) frequency c/(2d) = {f_alias:.1f} Hz; "
          f"low band free of spatial aliasing: {low_band_ok}")

    out = os.path.join(ARTIFACTS, "foa_yaw_report.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
    np.savetxt(os.path.join(ARTIFACTS, "yaw_rotation_matrix.csv"), R,
               delimiter=",", fmt="%.8f")
    np.savetxt(os.path.join(ARTIFACTS, "acn_sn3d_to_fuma_matrix.csv"), C_fwd,
               delimiter=",", fmt="%.8f")
    print(f"\nreport written to {out}")


if __name__ == "__main__":
    main()
