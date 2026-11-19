"""Pilot-tone based wow/flutter correction.

Pipeline: band-pass the pilot, take the analytic phase, differentiate to
instantaneous frequency, and normalise to a fractional speed curve v(t).
Gaps in the pilot are interpolated and flagged as *estimated intervals*;
a reviewer may bypass correction there. One strictly monotonic time map
is built from v(t) and shared by every program channel, so no programme
material can be inserted or dropped.
"""

from dataclasses import dataclass, field

import numpy as np
from scipy.interpolate import CubicHermiteSpline
from scipy.signal import butter, sosfiltfilt, hilbert

from .generate import PILOT_FREQ


@dataclass
class CorrectionResult:
    fs: int
    channels: dict                 # name to corrected signal
    speed: np.ndarray              # estimated fractional speed v(t)
    time_map: np.ndarray           # monotonic program-time per sample
    estimated_intervals: list      # (start_s, end_s) marked gap regions
    bypassed: bool                 # reviewer bypass applied on gaps
    valid_mask: np.ndarray = field(default=None)


def track_pilot_speed(pilot: np.ndarray, fs: int,
                      pilot_freq: float = PILOT_FREQ,
                      env_thresh: float = 0.05):
    """Return (speed v(t), valid_mask) from the pilot track."""
    half = 200.0
    sos = butter(4, [pilot_freq - half, pilot_freq + half],
                 btype="band", fs=fs, output="sos")
    band = sosfiltfilt(sos, pilot)
    analytic = hilbert(band)
    envelope = np.abs(analytic)

    # Envelope smoothed over ~10 ms decides where the pilot is present.
    k = max(1, int(0.010 * fs))
    kernel = np.ones(k) / k
    env_s = np.convolve(envelope, kernel, mode="same")
    valid = env_s > env_thresh * np.max(env_s)

    phase = np.unwrap(np.angle(analytic))
    inst_freq = np.gradient(phase, 1.0 / fs) / (2 * np.pi)
    speed = inst_freq / pilot_freq
    # Filter/Hilbert edge transients corrupt the first and last ~50 ms;
    # left alone they integrate into a constant timing offset.
    ne = int(0.050 * fs)
    speed[:ne] = speed[ne]
    speed[-ne:] = speed[-ne - 1]
    return speed, valid, phase


def _smooth_speed(speed: np.ndarray, fs: int, smooth_hz: float = 25.0):
    """Keep wow and flutter (up to ~20 Hz), reject tracking noise.

    Must run *after* gap filling: raw gap samples contain phase-noise
    spikes that a zero-phase filter would smear into valid regions.
    """
    sos_lp = butter(4, smooth_hz, fs=fs, output="sos")
    return sosfiltfilt(sos_lp, speed)


def _fill_gaps(speed: np.ndarray, valid: np.ndarray, fs: int,
               phase: np.ndarray = None, pilot_freq: float = PILOT_FREQ):
    """Interpolate speed across invalid runs; return (filled, intervals).

    Each gap is filled with a Hermite cubic matching the boundary values
    and slopes of the valid speed, then shifted by a constant so the
    *integral* of speed across the gap equals the phase advance observed
    at the gap boundaries. The anchor correction is applied as a raised-
    cosine bump (zero at both boundaries), so it removes the cumulative
    timing offset without introducing a step in the speed curve.
    """
    idx = np.arange(len(speed))
    filled = speed.copy()
    intervals = []
    # Erode the valid mask: samples near a dropout sit inside the pilot
    # fade and already carry phase noise, so treat them as gap too.
    ero = int(0.015 * fs)
    bad0 = np.where(~valid)[0]
    if len(bad0):
        valid = valid.copy()
        for b in bad0:
            valid[max(0, b - ero):b + ero] = False
    if valid.all():
        return filled, intervals
    bad = ~valid
    edges = np.diff(bad.astype(int))
    starts = list(np.where(edges == 1)[0] + 1)
    ends = list(np.where(edges == -1)[0] + 1)
    if bad[0]:
        starts = [0] + starts
    if bad[-1]:
        ends = ends + [len(bad)]
    # Wow-only reference for boundary values/slopes: raw flutter slopes
    # (~0.1/s) must not be extrapolated across a whole gap.
    pre = speed.copy()
    pre[bad] = np.interp(idx[bad], idx[valid], speed[valid])
    sos_wow = butter(4, 3.0, fs=fs, output="sos")
    sm = sosfiltfilt(sos_wow, pre)
    for s, e in zip(starts, ends):
        intervals.append((s / fs, e / fs))
        m = int(0.010 * fs)
        if s - m < 16 or len(speed) - e - m < 16:
            filled[s:e] = np.interp(idx[s:e], idx[valid], speed[valid])
            continue
        # Anchor on the wow-only reference 10 ms away from the edges.
        s0, e0 = s - m, e + m
        v0 = float(sm[s0])
        v1 = float(sm[e0])
        g = np.gradient(sm, 1.0 / fs)
        d0 = float(g[s0])
        d1 = float(g[e0])
        spline = CubicHermiteSpline([s0 / fs, e0 / fs], [v0, v1], [d0, d1])
        gap_fill = spline(idx[s:e] / fs)
        if phase is not None:
            # Boundary phases are trustworthy only mod 2*pi (unwrap runs
            # through gap noise), and only away from edge transients.
            if s - m >= 0 and e + m <= len(phase):
                ph0 = np.mean(phase[s - m:s - m + 16])
                ph1 = np.mean(phase[e + m - 16:e + m])
                frac = ((ph1 - ph0) % (2 * np.pi)) / (2 * np.pi)
                have_gap = np.trapezoid(gap_fill, dx=1.0 / fs)
                have_marg = np.trapezoid(speed[s - m:s], dx=1.0 / fs) \
                    + np.trapezoid(speed[e:e + m], dx=1.0 / fs)
                est_cycles = pilot_freq * (have_gap + have_marg)
                k = int(np.round(est_cycles - frac))
                need_gap = (k + frac) / pilot_freq - have_marg
                bump = 1 - np.cos(2 * np.pi * (np.arange(e - s) + 0.5)
                                  / (e - s))
                bump /= np.trapezoid(bump, dx=1.0 / fs)
                gap_fill = gap_fill + (need_gap - have_gap) * bump
        gap_fill = np.clip(gap_fill, 0.5, 1.5)
        filled[s:e] = gap_fill
    return filled, intervals


def build_time_map(speed: np.ndarray, fs: int) -> np.ndarray:
    """Integrate speed into a strictly monotonic program-time map."""
    speed = np.maximum(speed, 1e-6)  # forward tape motion only
    u = np.concatenate(([0.0], np.cumsum(speed[:-1]))) / fs
    return np.maximum.accumulate(u)


def _resample_with_map(signal: np.ndarray, time_map: np.ndarray,
                       fs: int) -> np.ndarray:
    """Evaluate signal at tape-times where the map crosses a uniform grid."""
    n = len(signal)
    u_target = np.arange(n) / fs
    u_clip = np.clip(u_target, time_map[0], time_map[-1])
    t_pos = np.interp(u_clip, time_map, np.arange(n))
    return np.interp(t_pos, np.arange(n), signal)


def correct_channels(channels: dict, pilot: np.ndarray, fs: int,
                     bypass_estimated: bool = False,
                     crossfade_ms: float = 10.0) -> CorrectionResult:
    """Correct every program channel with one shared monotonic map."""
    speed_raw, valid, phase = track_pilot_speed(pilot, fs)
    speed, intervals = _fill_gaps(speed_raw, valid, fs, phase)
    speed = _smooth_speed(speed, fs)
    time_map = build_time_map(speed, fs)

    out = {}
    for name, sig in channels.items():
        out[name] = _resample_with_map(sig, time_map, fs)

    if bypass_estimated and intervals:
        fade = int(crossfade_ms / 1000.0 * fs)
        for name, sig in channels.items():
            merged = out[name]
            for start_s, end_s in intervals:
                i0 = max(0, int(start_s * fs))
                i1 = min(len(sig), int(end_s * fs))
                # Crossfade the original uncorrected audio back in.
                w = np.zeros(len(sig))
                w[i0:i1] = 1.0
                if fade:
                    a0 = max(0, i0 - fade)
                    a1 = min(len(sig), i1 + fade)
                    w[a0:i0] = np.linspace(0, 1, i0 - a0)
                    w[i1:a1] = np.linspace(1, 0, a1 - i1)
                merged = (1 - w) * merged + w * sig
            out[name] = merged

    return CorrectionResult(fs=fs, channels=out, speed=speed,
                            time_map=time_map, estimated_intervals=intervals,
                            bypassed=bypass_estimated, valid_mask=valid)
