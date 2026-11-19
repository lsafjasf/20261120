"""Dynamic binaural rendering with head-rotation events and phase checks."""
import numpy as np
from scipy import signal

import hrtf_model as hm


def wrap180(x):
    return (np.asarray(x) + 180.0) % 360.0 - 180.0


def head_yaw_trajectory(t, events):
    """Piecewise-linear yaw (deg). events: list of (t_end, yaw_deg)."""
    yaw = np.zeros_like(t)
    t_prev, y_prev = 0.0, 0.0
    for t_end, y_end in events:
        m = (t >= t_prev) & (t < t_end)
        yaw[m] = y_prev + (y_end - y_prev) * (t[m] - t_prev) / (t_end - t_prev)
        t_prev, y_prev = t_end, y_end
    yaw[t >= t_prev] = y_prev
    return yaw


def render_dynamic(src, azimuth_track, grid_az, hrirs, fs=hm.FS,
                   block=512, hop=256, mode="equal_power"):
    """Overlap-add rendering with per-block interpolated HRIRs.

    azimuth_track: source-relative azimuth (deg) at each output sample.
    Returns (left, right) signals.
    """
    n_out = len(azimuth_track)
    win = signal.windows.hann(block, sym=False)
    left = np.zeros(n_out + hm.HRIR_LEN + block)
    right = np.zeros_like(left)
    pos = 0
    while pos < n_out:
        seg = src[pos:pos + block]
        if len(seg) < block:
            seg = np.pad(seg, (0, block - len(seg)))
        az_c = float(azimuth_track[min(pos + hop // 2, n_out - 1)])
        (hl, hr), _, _ = hm.interp_hrir(az_c, grid_az, hrirs, mode)
        seg = seg * win
        left[pos:pos + block + hm.HRIR_LEN - 1] += np.convolve(seg, hl)
        right[pos:pos + block + hm.HRIR_LEN - 1] += np.convolve(seg, hr)
        pos += hop
    return left[:n_out], right[:n_out]


def phase_continuity(x, fs, f0, bandwidth=400.0):
    """Instantaneous-phase analysis of a band-passed signal around f0.

    Returns dict with max phase jump (rad/sample vs expected), instantaneous
    angular-frequency sign flips (phase inversions), and the time series.
    """
    nyq = fs / 2
    lo, hi = max(f0 - bandwidth / 2, 20) / nyq, min(f0 + bandwidth / 2, nyq - 20) / nyq
    b, a = signal.butter(4, [lo, hi], btype="band")
    xb = signal.filtfilt(b, a, x)
    analytic = signal.hilbert(xb)
    phase = np.unwrap(np.angle(analytic))
    inst_w = np.diff(phase)  # rad/sample
    # Discard filter/hilbert edge transients and low-amplitude samples.
    edge = int(0.1 * fs)
    amp = np.abs(analytic)
    valid = np.zeros_like(inst_w, dtype=bool)
    valid[edge:-edge] = True
    valid &= amp[:-1] > 0.2 * np.max(amp)
    w0 = 2 * np.pi * f0 / fs
    # Phase jump: deviation of one-sample increment from expected rotation.
    jumps = np.abs(inst_w[valid] - w0)
    # Phase inversion: instantaneous frequency changes sign.
    inversions = int(np.sum(inst_w[valid] < 0))
    return {
        "phase": phase,
        "inst_w": inst_w,
        "valid": valid,
        "max_jump_rad": float(np.max(jumps)),
        "inversions": inversions,
        "expected_w": w0,
    }


def block_boundary_phase(x, fs, f0, hop=256):
    """Phase increment across block boundaries vs expected f0 rotation."""
    b, a = signal.butter(4, [0.9 * f0 / (fs / 2), min(1.1 * f0 / (fs / 2), 0.99)],
                         btype="band")
    xb = signal.filtfilt(b, a, x)
    phase = np.unwrap(np.angle(signal.hilbert(xb)))
    idx = np.arange(hop, len(phase) - hop, hop)
    inc = np.diff(phase[idx])
    expected = 2 * np.pi * f0 * hop / fs
    return np.max(np.abs(inc - expected)), inc
