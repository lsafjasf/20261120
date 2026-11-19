"""Objective metrics computed over rendered :class:`RenderState` streams."""

import math

from .filters import angle_diff


def percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return float("nan")
    idx = min(len(sorted_values) - 1, int(p * (len(sorted_values) - 1)))
    return sorted_values[idx]


def static_jitter_deg(states, t0: float, t1: float) -> float:
    """Std-dev of filtered yaw while the head is nominally stationary."""
    yaws = [s.filtered_yaw for s in states if t0 <= s.t <= t1]
    if len(yaws) < 2:
        return 0.0
    mean = sum(yaws) / len(yaws)
    var = sum((y - mean) ** 2 for y in yaws) / (len(yaws) - 1)
    return math.degrees(math.sqrt(var))


def raw_static_jitter_deg(states, t0: float, t1: float) -> float:
    yaws = [s.raw_yaw for s in states if t0 <= s.t <= t1]
    if len(yaws) < 2:
        return 0.0
    mean = sum(yaws) / len(yaws)
    var = sum((y - mean) ** 2 for y in yaws) / (len(yaws) - 1)
    return math.degrees(math.sqrt(var))


def pan_step_rms_db(states) -> float:
    """RMS per-tick change of the left pan gain, in dB (zipper measure)."""
    deltas = []
    prev = None
    for s in states:
        if prev is not None:
            ratio = max(s.gain_l, 1e-6) / max(prev, 1e-6)
            deltas.append(20.0 * math.log10(ratio))
        prev = s.gain_l
    if not deltas:
        return 0.0
    return math.sqrt(sum(d * d for d in deltas) / len(deltas))


def doppler_jump_stats(states, slew: bool) -> dict[str, float]:
    """Largest single-tick pitch jump (cents) and count of audible jumps.

    A jump beyond ~15-20 cents is clearly audible as a pitch zipper.
    """
    key = "doppler_applied" if slew else "doppler_target"
    cents = [1200.0 * math.log2(getattr(s, key)) for s in states]
    jumps = [abs(b - a) for a, b in zip(cents, cents[1:])]
    ordered = sorted(jumps)
    return {
        "max_jump_cents": max(ordered, default=0.0),
        "p95_jump_cents": percentile(ordered, 0.95),
        "jumps_over_15c": float(sum(1 for j in jumps if j > 15.0)),
        "peak_cents": max((abs(c) for c in cents), default=0.0),
    }


def tracking_lag_ms(
    states, true_yaw_fn, during=(0.6, 1.8)
) -> dict[str, float]:
    """Estimate tracking lag by best-shift alignment on the render grid."""
    window = [s for s in states if during[0] <= s.t <= during[1]]
    if len(window) < 8:
        return {"lag_ms": 0.0, "rms_error_deg": 0.0}
    dt = window[1].t - window[0].t
    true_series = [true_yaw_fn(s.t) for s in window]
    filt_series = [s.filtered_yaw for s in window]

    max_shift = min(int(0.2 / dt), len(window) // 3)
    best = (float("inf"), 0, 0.0)
    for shift in range(-max_shift, max_shift + 1):
        # shift > 0: filtered lags (compare filtered[i] with true[i-shift])
        pairs = []
        for i, f in enumerate(filt_series):
            j = i - shift
            if 0 <= j < len(true_series):
                pairs.append(angle_diff(f, true_series[j]))
        if len(pairs) < 4:
            continue
        energy = sum(p * p for p in pairs) / len(pairs)
        if energy < best[0]:
            best = (energy, shift, 0.0)
        # parabolic sub-sample refinement around integer minimum
    energy0, shift0, _ = best
    if abs(shift0) < max_shift:
        e_prev = e_next = None
        for shift in range(-max_shift, max_shift + 1):
            pairs = [
                angle_diff(f, true_series[i - shift])
                for i, f in enumerate(filt_series)
                if 0 <= i - shift < len(true_series)
            ]
            e = sum(p * p for p in pairs) / len(pairs)
            if shift == shift0 - 1:
                e_prev = e
            if shift == shift0 + 1:
                e_next = e
        if e_prev is not None and e_next is not None:
            denom = e_prev - 2 * energy0 + e_next
            if abs(denom) > 1e-15:
                frac = 0.5 * (e_prev - e_next) / denom
                frac = max(-1.0, min(1.0, frac))
                best = (energy0, shift0, frac)
    energy0, shift0, frac = best
    lag_ms = (shift0 + frac) * dt * 1000.0
    return {
        "lag_ms": lag_ms,
        "rms_error_deg": math.degrees(math.sqrt(energy0)),
    }


def update_rate_curve(states, window_s: float = 0.25):
    """Sliding-window relationship between rotation speed and update rate.

    For each window we report three frequencies:

    * ``applied_hz``  - render ticks that consumed a sensor event
    * ``changed_hz``  - ticks whose filtered azimuth actually moved
    * ``render_hz``   - fixed audio render cadence

    plus the mean absolute angular speed in the window. Applied rate stays
    pinned to the sensor rate (events are never dropped), while the
    smoothed mode's *changed* rate falls at rest and rises with speed.
    """
    if len(states) < 2:
        return []
    render_hz = render_rate_hz(states)
    half = window_s / 2.0
    curve = []
    for anchor in states[:: max(1, int(render_hz * window_s) // 2)]:
        lo_t, hi_t = anchor.t - half, anchor.t + half
        win = [s for s in states if lo_t <= s.t < hi_t]
        if len(win) < 3:
            continue
        span = win[-1].t - win[0].t
        if span <= 0:
            continue
        applied = sum(1 for s in win if s.applied_seq is not None)
        changed = sum(
            1 for a, b in zip(win, win[1:])
            if abs(angle_diff(b.filtered_yaw, a.filtered_yaw)) > 1e-5
        )
        speed = sum(abs(math.degrees(s.angular_velocity)) for s in win) / len(win)
        curve.append({
            "t": anchor.t,
            "speed_deg_s": speed,
            "applied_hz": applied / span,
            "changed_hz": changed / span,
            "render_hz": render_hz,
            "mean_queue_depth": sum(s.queue_depth for s in win) / len(win),
        })
    return curve


def max_queue_depth(states) -> int:
    return max((s.queue_depth for s in states), default=0)


def render_rate_hz(states) -> float:
    if len(states) < 2:
        return 0.0
    return (len(states) - 1) / (states[-1].t - states[0].t)
