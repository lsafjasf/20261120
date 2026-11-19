"""Scoring: residual frequency deviation, L/R alignment, gap continuity,
and proof that correction inserted or dropped no programme material.
"""

import numpy as np
from scipy.signal import butter, sosfiltfilt, fftconvolve

from .correct import track_pilot_speed
from .generate import PILOT_FREQ


def residual_deviation(pilot: np.ndarray, fs: int,
                       pilot_freq: float = PILOT_FREQ) -> dict:
    """Wow/flutter style stats of the pilot's instantaneous frequency."""
    speed, valid, _ = track_pilot_speed(pilot, fs)
    dev = (speed - 1.0) * 100.0  # percent
    edge = int(0.1 * fs)
    m = valid.copy()
    m[:edge] = False
    m[-edge:] = False
    # Erode 30 ms around invalid runs: edge transients carry phase garbage.
    ero = int(0.030 * fs)
    bad_idx = np.where(~m)[0]
    for b in bad_idx:
        m[max(0, b - ero):b + ero] = False
    d = dev[m]
    # Fill invalid stretches before band-splitting, else filter tails
    # smear edge garbage across the whole timeline.
    dev_f = dev.copy()
    good = np.where(m)[0]
    dev_f[~m] = np.interp(np.where(~m)[0], good, dev[good])
    sos_wow = butter(4, 5.0, fs=fs, output="sos")
    sos_fl = butter(4, [5.0, 20.0], btype="band", fs=fs, output="sos")
    wow = sosfiltfilt(sos_wow, dev_f)[m]
    flut = sosfiltfilt(sos_fl, dev_f)[m]
    return {
        "rms_pct": float(np.sqrt(np.mean(d ** 2))),
        "peak_pct": float(np.max(np.abs(d))),
        "wow_rms_pct": float(np.sqrt(np.mean(wow ** 2))),
        "flutter_rms_pct": float(np.sqrt(np.mean(flut ** 2))),
    }


def lag_series(left: np.ndarray, right: np.ndarray, fs: int,
               win_ms: float = 50.0, max_lag_ms: float = 2.0):
    """Sub-sample L/R lag per window via parabolic-refined xcorr."""
    sos = butter(4, [900.0, 1100.0], btype="band", fs=fs, output="sos")
    l = sosfiltfilt(sos, left)
    r = sosfiltfilt(sos, right)
    win = int(win_ms / 1000.0 * fs)
    max_lag = int(max_lag_ms / 1000.0 * fs)
    times, lags = [], []
    for s in range(0, len(l) - win, win // 2):
        a = l[s:s + win]
        b = r[s:s + win]
        c = np.correlate(a, b, mode="full")
        c = c[len(c) // 2 - max_lag: len(c) // 2 + max_lag + 1]
        k = int(np.argmax(c)) - max_lag
        ki = k + max_lag
        if 0 < ki < len(c) - 1:
            denom = c[ki - 1] - 2 * c[ki] + c[ki + 1]
            if abs(denom) > 1e-12:
                k = k + 0.5 * (c[ki - 1] - c[ki + 1]) / denom
        times.append((s + win / 2) / fs)
        lags.append(k / fs)
    return np.array(times), np.array(lags)


def alignment_error(left: np.ndarray, right: np.ndarray, fs: int,
                    truth_lag: np.ndarray = None,
                    truth_times: np.ndarray = None) -> dict:
    """L/R misalignment stats in milliseconds.

    If the ground-truth lag (right minus left, seconds) is given, report
    the estimation error against it; otherwise report the raw lag spread.
    """
    times, lags = lag_series(left, right, fs)
    out = {
        "mean_abs_lag_ms": float(np.mean(np.abs(lags)) * 1e3),
        "max_abs_lag_ms": float(np.max(np.abs(lags)) * 1e3),
    }
    if truth_lag is not None:
        true = np.interp(times, truth_times, truth_lag)
        err = (lags - true) * 1e3
        out["est_err_rms_ms"] = float(np.sqrt(np.mean(err ** 2)))
        out["residual_truth_rms_ms"] = float(np.sqrt(np.mean((true * 1e3) ** 2)))
    return out


def gap_continuity(signal: np.ndarray, fs: int, intervals: list,
                   margin_ms: float = 5.0) -> dict:
    """Detect step discontinuities near estimated-interval boundaries."""
    diff = np.abs(np.diff(signal))
    margin = int(margin_ms / 1000.0 * fs)
    near = np.zeros(len(diff), bool)
    for start_s, end_s in intervals:
        for b in (start_s, end_s):
            i = int(b * fs)
            near[max(0, i - margin):i + margin] = True
    normal = diff[~near]
    thresh = 5.0 * np.percentile(normal, 99.9)
    viol = int(np.sum(diff[near] > thresh)) if near.any() else 0
    return {
        "threshold": float(thresh),
        "max_jump_near_gaps": float(np.max(diff[near])) if near.any() else 0.0,
        "violations": viol,
    }


def integrity_check(original: dict, corrected: dict, time_map: np.ndarray,
                    clean: dict, fs: int, skip: list = None) -> dict:
    """Prove no programme was inserted or dropped by the correction."""
    same_len = all(len(corrected[k]) == len(original[k]) for k in original)
    mono = bool(np.all(np.diff(time_map) > 0))
    span_s = float(time_map[-1] - time_map[0])
    # Per-second best-lag correlation of corrected vs clean source: every
    # second of programme must be found, at one consistent lag per segment.
    min_corr = {}
    lag_steps = {}
    sec = fs
    max_lag = int(0.050 * fs)
    for name, ref in clean.items():
        sig = corrected[name]
        corrs, lags, centers = [], [], []
        for s in range(0, len(sig) - sec, sec):
            tmid = (s + sec / 2) / fs
            a = sig[s:s + sec] - np.mean(sig[s:s + sec])
            b = ref[s:s + sec] - np.mean(ref[s:s + sec])
            denom = np.linalg.norm(a) * np.linalg.norm(b)
            c = fftconvolve(a, b[::-1])
            c = c[len(c) // 2 - max_lag: len(c) // 2 + max_lag + 1]
            k = int(np.argmax(c)) - max_lag
            corrs.append(float(c[max_lag + k] / denom) if denom > 0 else 0.0)
            lags.append(k / fs)
            centers.append(tmid)
        corrs = np.array(corrs)
        lags = np.array(lags)
        centers = np.array(centers)
        in_gap = np.zeros(len(centers), bool)
        if skip:
            for a0, b0 in skip:
                in_gap |= (centers > a0 - 0.6) & (centers < b0 + 0.6)
        outside = ~in_gap
        min_corr[name] = float(np.min(corrs[outside])) if outside.any() \
            else float("nan")
        # Lag must not jump outside estimated intervals: a jump would mean
        # a programme fragment was inserted or dropped there.
        steps = np.abs(np.diff(lags)) * 1e3
        step_ok = np.where(outside[:-1] & outside[1:])[0]
        lag_steps[name] = float(np.max(steps[step_ok])) if len(step_ok) \
            else 0.0
    return {
        "lengths_preserved": bool(same_len),
        "map_strictly_monotonic": mono,
        "map_span_s": span_s,
        "min_per_second_corr": min_corr,
        "max_lag_step_ms_outside_gaps": lag_steps,
    }
