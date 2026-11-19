"""AEC simulation: NLMS adaptive filter with double-talk detector.

Experiments:
  A. Baseline convergence: ERLE curve, steady-state ERLE, convergence time.
  B. Parameter sweep: step size mu x filter length L.
  C. Double-talk: near-end speech retention rate (no near-end erasure).
  D. Echo-path change: filter re-converges after the path switches.
  E. Dual-track WAV round trip: inter-channel time offset preserved.
"""

import os
import wave

import numpy as np

FS = 16000
OUT_DIR = "aec_results"


def synth_speech(dur, seed, f0=140.0):
    """Speech-like signal: harmonic source with syllabic on/off envelope."""
    rng = np.random.default_rng(seed)
    n = int(dur * FS)
    t = np.arange(n) / FS
    env = np.zeros(n)
    pos = 0
    while pos < n:
        seg = int(rng.uniform(0.08, 0.30) * FS)
        gap = int(rng.uniform(0.02, 0.12) * FS)
        env[pos:pos + seg] = rng.uniform(0.5, 1.0)
        pos += seg + gap
    k = np.arange(1, 8)
    phase = rng.uniform(0, 2 * np.pi, k.shape)
    src = np.sin(2 * np.pi * np.outer(t, f0 * k) + phase).sum(axis=1)
    src += 0.3 * rng.standard_normal(n)
    env = np.convolve(env, np.ones(80) / 80, mode="same")
    sig = src * env
    return sig / (np.max(np.abs(sig)) + 1e-12)


def make_echo_path(length, seed, delay=24, decay_ms=40.0):
    """Sparse-decay FIR room impulse response."""
    rng = np.random.default_rng(seed)
    h = np.zeros(length)
    tail = rng.standard_normal(length - delay)
    tail *= np.exp(-np.arange(length - delay) / (decay_ms * FS / 1000.0 / 3))
    h[delay:] = tail
    return h / (np.linalg.norm(h) + 1e-12)


def geigel_dtd(x, d, thresh=0.5, hangover=240):
    """Geigel double-talk detector: returns boolean mask, True = double talk."""
    n = len(x)
    mask = np.zeros(n, dtype=bool)
    win = np.abs(x[:512].copy())
    hold = 0
    for i in range(n):
        win[:-1] = win[1:]
        win[-1] = abs(x[i])
        if abs(d[i]) >= thresh * np.max(win):
            hold = hangover
        if hold > 0:
            mask[i] = True
            hold -= 1
    return mask


def nlms(x, d, L, mu, eps=1e-4, freeze=None):
    """Sample-by-sample NLMS. freeze[i]=True skips the weight update."""
    n = len(x)
    w = np.zeros(L)
    xbuf = np.zeros(L)
    e = np.zeros(n)
    for i in range(n):
        xbuf[1:] = xbuf[:-1]
        xbuf[0] = x[i]
        y = w @ xbuf
        e[i] = d[i] - y
        if freeze is None or not freeze[i]:
            w += mu * e[i] * xbuf / (xbuf @ xbuf + eps)
    return e, w


def erle_curve(d, e, win=800):
    """Smoothed ERLE in dB (50 ms window at 16 kHz)."""
    p_d = np.convolve(d ** 2, np.ones(win) / win, mode="same")
    p_e = np.convolve(e ** 2, np.ones(win) / win, mode="same")
    return 10 * np.log10((p_d + 1e-12) / (p_e + 1e-12))


def convergence_time(d, e, thresh_db=20.0, hold_s=0.5, seg_s=0.05):
    """Segment-based convergence time (robust to speech pauses).

    ERLE is evaluated per 50 ms segment on active speech only; convergence
    is the first time ERLE stays >= thresh_db for a hold_s window.
    """
    seg = int(seg_s * FS)
    nseg = len(d) // seg
    act_thresh = 0.01 * float(np.mean(d ** 2))
    erle_seg = np.full(nseg, np.nan)
    for i in range(nseg):
        dd = d[i * seg:(i + 1) * seg]
        ee = e[i * seg:(i + 1) * seg]
        if np.mean(dd ** 2) > act_thresh:
            erle_seg[i] = 10 * np.log10(np.sum(dd ** 2) /
                                        (np.sum(ee ** 2) + 1e-12))
    hold = max(1, int(hold_s / seg_s))
    for i in range(nseg - hold):
        win = erle_seg[i:i + hold]
        active = win[~np.isnan(win)]
        if len(active) >= max(1, hold // 2) and np.all(active >= thresh_db):
            return i * seg_s
    return None


def build_scene(dur, seed, near_segments=None, path_seed=7):
    """Far-end signal, echo path, mic signal d = echo + near-end."""
    far = synth_speech(dur, seed, f0=140.0)
    h = make_echo_path(256, path_seed)
    echo = np.convolve(far, h)[:len(far)] * 0.8
    near = np.zeros(len(far))
    if near_segments:
        speech = synth_speech(dur, seed + 1000, f0=220.0)
        for t0, t1 in near_segments:
            near[int(t0 * FS):int(t1 * FS)] = \
                speech[int(t0 * FS):int(t1 * FS)] * 0.6
    return far, h, echo, near, echo + near


def exp_a_baseline():
    print("=" * 64)
    print("[A] Baseline: NLMS mu=0.5, L=256, far-end only (no double talk)")
    far, h, echo, _, d = build_scene(8.0, seed=1)
    e, _ = nlms(far, d, L=256, mu=0.5)
    erle = erle_curve(d, e)
    ct = convergence_time(d, e)
    steady = float(np.mean(erle[int(6.0 * FS):int(7.5 * FS)]))
    print(f"  steady-state ERLE (6.0-7.5 s avg) : {steady:6.2f} dB")
    print(f"  convergence time (>=20 dB, 0.5 s) : "
          f"{ct if ct is not None else float('nan'):.3f} s")
    return erle


def exp_b_sweep():
    print("=" * 64)
    print("[B] Sweep: step size mu x filter length L (5 s far-end only)")
    far, h, echo, _, d = build_scene(5.0, seed=2)
    header = "  mu\\L  |" + "".join(f"  L={L:<5}" for L in (128, 256, 512, 1024))
    print(header + "    (steady ERLE dB / conv. time s)")
    print("  (echo path = 256 taps: L=128 is under-modeled on purpose)")
    results = {}
    for mu in (0.1, 0.3, 0.5, 0.8):
        row = f"  {mu:<5.1f} |"
        for L in (128, 256, 512, 1024):
            e, _ = nlms(far, d, L=L, mu=mu)
            erle = erle_curve(d, e)
            ct = convergence_time(d, e)
            steady = float(np.mean(erle[int(4.0 * FS):int(4.8 * FS)]))
            results[(mu, L)] = (steady, ct)
            ct_s = f"{ct:4.2f}" if ct is not None else "  n/a"
            row += f"  {steady:5.1f}/{ct_s}"
        print(row)
    return results


def exp_c_double_talk():
    print("=" * 64)
    print("[C] Double talk (3.0-5.0 s): near-end speech retention")
    dt_seg = (3.0, 5.0)
    far, h, echo, near, d = build_scene(8.0, seed=3,
                                        near_segments=[dt_seg])
    dt_mask = geigel_dtd(far, d)
    e, _ = nlms(far, d, L=256, mu=0.5, freeze=dt_mask)
    s0, s1 = int(dt_seg[0] * FS), int(dt_seg[1] * FS)
    e_dt, n_dt = e[s0:s1], near[s0:s1]
    active = np.abs(n_dt) > 1e-3
    alpha = float(e_dt[active] @ n_dt[active] / (n_dt[active] @ n_dt[active]))
    retained = alpha ** 2 * float(np.sum(n_dt[active] ** 2))
    retention = retained / float(np.sum(n_dt[active] ** 2))
    resid = e_dt[active] - alpha * n_dt[active]
    resid_db = 10 * np.log10(np.sum(resid ** 2) / np.sum(n_dt[active] ** 2))
    print(f"  DTD freeze ratio in DT segment    : "
          f"{np.mean(dt_mask[s0:s1]) * 100:5.1f} %")
    print(f"  near-end retention rate           : "
          f"{retention * 100:6.2f} % ({10 * np.log10(max(retention, 1e-9)):5.2f} dB)")
    print(f"  residual (echo leak) vs near-end  : {resid_db:6.2f} dB")
    ok = retention > 0.90
    print(f"  => near-end speech {'PRESERVED' if ok else 'DAMAGED'} "
          f"(threshold 90 %)")
    return retention


def exp_d_path_change():
    print("=" * 64)
    print("[D] Echo-path change at t=4.0 s: re-convergence check")
    far = synth_speech(8.0, seed=4, f0=140.0)
    h1 = make_echo_path(256, seed=7)
    h2 = make_echo_path(256, seed=77, delay=64, decay_ms=60.0)
    echo1 = np.convolve(far, h1)[:len(far)] * 0.8
    echo2 = np.convolve(far, h2)[:len(far)] * 0.8
    cut = int(4.0 * FS)
    d = np.concatenate([echo1[:cut], echo2[cut:]])
    e, _ = nlms(far, d, L=256, mu=0.5)
    erle = erle_curve(d, e)
    ct1 = convergence_time(d[:cut], e[:cut])
    ct2 = convergence_time(d[cut:], e[cut:])
    steady2 = float(np.mean(erle[int(6.5 * FS):int(7.8 * FS)]))
    dip = float(np.min(erle[cut:cut + int(0.2 * FS)]))
    print(f"  first convergence (path 1)        : "
          f"{ct1 if ct1 is not None else float('nan'):.3f} s")
    print(f"  ERLE dip right after path change  : {dip:6.2f} dB")
    print(f"  re-convergence after path change  : "
          f"{ct2 if ct2 is not None else float('nan'):.3f} s")
    print(f"  steady-state ERLE (6.5-7.8 s)     : {steady2:6.2f} dB")
    ok = ct2 is not None and steady2 > 20.0
    print(f"  => filter {'RE-CONVERGED' if ok else 'FAILED to re-converge'}")
    return ct2


def write_stereo_wav(path, left, right):
    pcm = np.stack([left, right], axis=1)
    pcm = np.clip(pcm, -1.0, 1.0)
    pcm = (pcm * 32767).astype("<i2")
    with wave.open(path, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(FS)
        wf.writeframes(pcm.tobytes())


def read_stereo_wav(path):
    with wave.open(path, "rb") as wf:
        raw = wf.readframes(wf.getnframes())
    pcm = np.frombuffer(raw, dtype="<i2").reshape(-1, 2)
    return pcm[:, 0] / 32768.0, pcm[:, 1] / 32768.0


def estimate_offset(ref, sig, max_lag=4000):
    """Lag of sig vs ref via cross-correlation (samples)."""
    n = len(ref)
    nfft = 1 << (2 * n - 1).bit_length()
    R = np.fft.rfft(ref, nfft)
    S = np.fft.rfft(sig, nfft)
    xc = np.fft.irfft(S * np.conj(R), nfft)
    lags = np.concatenate([np.arange(0, max_lag + 1),
                           np.arange(nfft - max_lag, nfft) - nfft])
    vals = np.concatenate([xc[:max_lag + 1], xc[nfft - max_lag:]])
    return int(lags[np.argmax(vals)])


def exp_e_roundtrip():
    print("=" * 64)
    print("[E] Dual-track WAV round trip: inter-channel time offset")
    os.makedirs(OUT_DIR, exist_ok=True)
    far, h, echo, near, d = build_scene(4.0, seed=5,
                                        near_segments=[(2.0, 3.0)])
    true_lag = int(np.argmax(np.abs(h)))
    off_src = estimate_offset(far, d)
    wav_path = os.path.join(OUT_DIR, "dual_track.wav")
    write_stereo_wav(wav_path, far, d)
    far_rt, d_rt = read_stereo_wav(wav_path)
    off_rt = estimate_offset(far_rt, d_rt)
    diff_samples = off_rt - off_src
    diff_ms = diff_samples / FS * 1000.0
    print(f"  true echo delay (argmax |h|)      : {true_lag} samples")
    print(f"  offset before round trip          : {off_src} samples")
    print(f"  offset after  round trip          : {off_rt} samples")
    print(f"  timestamp diff                    : {diff_samples} samples "
          f"({diff_ms:+.3f} ms)")
    ok = diff_samples == 0
    print(f"  => time offset {'PRESERVED' if ok else 'SHIFTED'} "
          f"by WAV round trip")
    return diff_samples


def main():
    exp_a_baseline()
    exp_b_sweep()
    exp_c_double_talk()
    exp_d_path_change()
    exp_e_roundtrip()
    print("=" * 64)
    print("All experiments finished.")


if __name__ == "__main__":
    main()
