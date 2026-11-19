"""Spherical-head HRTF model with azimuth interpolation and estimation.

Convention: azimuth in degrees, 0 = front, +90 = left, -90 = right.
"""
import numpy as np

C_SOUND = 343.0
HEAD_RADIUS = 0.0875
FS = 48000
HRIR_LEN = 512
BASE_DELAY = 128  # samples, makes fractional delays causal


def woodworth_itd(az_deg):
    """Interaural time difference (s). Positive => left ear leads."""
    th = np.deg2rad(np.asarray(az_deg, dtype=float))
    a = HEAD_RADIUS
    mag = np.abs(th)
    itd_mag = np.where(
        mag <= np.pi / 2,
        (a / C_SOUND) * (mag + np.sin(mag)),
        (a / C_SOUND) * (np.pi - mag + np.sin(mag)),
    )
    return np.sign(th) * itd_mag


def azimuth_from_itd(itd_s, grid=None):
    """Invert Woodworth ITD via a monotonic lookup on |azimuth|."""
    if grid is None:
        grid = np.linspace(0.0, 90.0, 9001)  # monotonic frontal hemisphere
    lut = woodworth_itd(grid)
    est = np.interp(np.abs(itd_s), lut, grid)
    return np.sign(itd_s) * est


def hrir(az_deg, n=HRIR_LEN, fs=FS):
    """Synthetic binaural HRIR (left, right) for a given azimuth."""
    freqs = np.fft.rfftfreq(n, 1.0 / fs)
    itd = woodworth_itd(az_deg)
    tau_l = BASE_DELAY / fs - itd / 2.0
    tau_r = BASE_DELAY / fs + itd / 2.0
    # Head shadowing: far ear gets a low-pass whose cutoff drops as the
    # source moves to the side, plus a few dB of extra loss.
    sin_az = abs(np.sin(np.deg2rad(az_deg)))
    fc = 12000.0 - 9500.0 * sin_az  # 12 kHz front -> 2.5 kHz side
    shadow = 1.0 / (1.0 + 1j * freqs / fc)
    shadow *= 10.0 ** (-3.0 * sin_az / 20.0)
    if az_deg >= 0:  # source on the left: right ear is shadowed
        mag_l, mag_r = np.ones_like(freqs), shadow
    else:
        mag_l, mag_r = shadow, np.ones_like(freqs)
    hl = np.fft.irfft(mag_l * np.exp(-2j * np.pi * freqs * tau_l), n)
    hr = np.fft.irfft(mag_r * np.exp(-2j * np.pi * freqs * tau_r), n)
    return hl, hr


def crossfade_hrir(h1, h2, w, mode="equal_power"):
    """Interpolate two HRIR pairs. w in [0, 1]; w=0 -> h1, w=1 -> h2."""
    if mode == "linear":
        a, b = 1.0 - w, w
    else:  # equal-power
        a, b = np.cos(w * np.pi / 2), np.sin(w * np.pi / 2)
    return a * h1[0] + b * h2[0], a * h1[1] + b * h2[1]


def interp_hrir(az_deg, grid_az, hrirs, mode="equal_power"):
    """Interpolate the HRIR at an arbitrary azimuth from a sampled grid."""
    az_deg = float(np.clip(az_deg, grid_az[0], grid_az[-1]))
    idx = int(np.searchsorted(grid_az, az_deg, side="right")) - 1
    idx = min(max(idx, 0), len(grid_az) - 2)
    w = (az_deg - grid_az[idx]) / (grid_az[idx + 1] - grid_az[idx])
    return crossfade_hrir(hrirs[idx], hrirs[idx + 1], w, mode), w, idx


def build_grid(step_deg=15.0, az_min=-180.0, az_max=180.0, fs=FS):
    grid_az = np.arange(az_min, az_max + 0.5 * step_deg, step_deg)
    hrirs = [hrir(a, fs=fs) for a in grid_az]
    return grid_az, hrirs


def estimate_itd(hl, hr, fs=FS, max_lag_ms=1.2):
    """GCC-PHAT ITD estimate with parabolic sub-sample refinement."""
    n = len(hl) + len(hr) - 1
    nfft = 1 << (n - 1).bit_length()
    H1 = np.fft.rfft(hl, nfft)
    H2 = np.fft.rfft(hr, nfft)
    R = H1 * np.conj(H2)
    R /= np.maximum(np.abs(R), 1e-12)
    cc = np.fft.irfft(R, nfft)
    max_lag = int(max_lag_ms * 1e-3 * fs)
    cc = np.concatenate([cc[-max_lag:], cc[: max_lag + 1]])
    k = int(np.argmax(cc)) - max_lag
    if 0 < k + max_lag < len(cc) - 1:
        y0, y1, y2 = cc[k + max_lag - 1], cc[k + max_lag], cc[k + max_lag + 1]
        denom = y0 - 2 * y1 + y2
        if abs(denom) > 1e-12:
            k = k + 0.5 * (y0 - y2) / denom
    return -k / fs  # positive => left ear leads
