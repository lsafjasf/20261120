"""Variable-speed (time-stretch) matching-offset linearity check."""
import numpy as np
from scipy import signal


def make_probe(fs=48000, dur=4.0, f0=300.0, f1=4000.0):
    t = np.arange(int(fs * dur)) / fs
    return signal.chirp(t, f0, t[-1], f1, method="logarithmic"), t


def stretch(x, rate):
    """y(t) = x(t / rate): rate>1 slows down (longer output)."""
    n_out = int(round(len(x) * rate))
    return signal.resample(x, n_out)


def match_offsets(x, y, fs, rate, win_s=0.1, step_s=0.2, margin_s=0.6):
    """Cross-correlate windows of x against y; return times and offsets."""
    win = int(win_s * fs)
    times, offsets = [], []
    t = margin_s
    while t + win_s < len(x) / fs - margin_s:
        i0 = int(t * fs)
        templ = x[i0:i0 + win]
        lo = max(0, int((t * rate - margin_s) * fs))
        hi = min(len(y), int(((t + win_s) * rate + margin_s) * fs))
        seg = y[lo:hi]
        if len(seg) < win:
            break
        cc = signal.fftconvolve(seg, templ[::-1], mode="valid")
        pos_y = (lo + int(np.argmax(cc))) / fs
        times.append(t)
        offsets.append(pos_y - t)
        t += step_s
    return np.array(times), np.array(offsets)


def check_linearity(fs=48000, rates=(0.75, 1.25, 1.5)):
    x, _ = make_probe(fs=fs)
    results = []
    for rate in rates:
        y = stretch(x, rate)
        times, offsets = match_offsets(x, y, fs, rate)
        # Expected: matched position = rate * t  =>  offset = (rate - 1) * t
        slope, intercept = np.polyfit(times, offsets, 1)
        pred = slope * times + intercept
        ss_res = float(np.sum((offsets - pred) ** 2))
        ss_tot = float(np.sum((offsets - offsets.mean()) ** 2))
        r2 = 1.0 - ss_res / max(ss_tot, 1e-12)
        results.append({
            "rate": rate,
            "slope": float(slope),
            "expected_slope": rate - 1.0,
            "slope_err": float(slope - (rate - 1.0)),
            "intercept": float(intercept),
            "r2": r2,
            "n_windows": len(times),
        })
    return results
