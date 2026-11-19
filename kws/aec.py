"""Echo cancellation with an NLMS adaptive filter and ERLE measurement."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


class NLMS:
    def __init__(self, taps: int = 320, mu: float = 1.0,
                 leakage: float = 1e-5):
        self.taps = taps
        self.mu = mu
        self.leakage = leakage
        self.weights = np.zeros(taps)
        self.buffer = np.zeros(taps)

    def step(self, reference: float, desired: float) -> tuple[float, float]:
        self.buffer[1:] = self.buffer[:-1]
        self.buffer[0] = reference
        estimate = float(self.weights @ self.buffer)
        error = desired - estimate
        norm = float(self.buffer @ self.buffer) + 1e-6
        self.weights *= (1.0 - self.leakage)
        self.weights += (self.mu * error / norm) * self.buffer
        return error, estimate

    def reset(self):
        self.weights.fill(0.0)
        self.buffer.fill(0.0)


def make_echo_path(fs: int, seed: int, delay_ms: float = 20.0) -> np.ndarray:
    """Sparse room-like impulse response with an initial bulk delay."""
    rng = np.random.default_rng(seed)
    delay = int(delay_ms * fs / 1000.0)
    tail_n = 96
    t = np.arange(tail_n) / fs
    decay = np.exp(-6.0 * t / (tail_n / fs))
    ir = rng.standard_normal(tail_n) * decay
    ir[::5] *= 2.5  # sparse strong reflections
    full = np.concatenate([np.zeros(delay), ir])
    return full / np.sqrt(np.sum(full ** 2))


def block_erle(mic: np.ndarray, err: np.ndarray, fs: int,
               block_ms: float = 40.0) -> np.ndarray:
    block = int(block_ms * fs / 1000.0)
    n_blocks = len(mic) // block
    erle = np.zeros(n_blocks)
    for k in range(n_blocks):
        seg_m = mic[k * block:(k + 1) * block]
        seg_e = err[k * block:(k + 1) * block]
        erle[k] = 10.0 * np.log10(
            (np.mean(seg_m ** 2) + 1e-12) / (np.mean(seg_e ** 2) + 1e-12)
        )
    return erle


@dataclass
class AECResult:
    erle_curve: np.ndarray
    erle_pre: float
    erle_transient: float
    erle_reconverged: float
    misalignment_curve: np.ndarray
    reconverge_time_s: float
    change_sample: int
    misalignment_converged_db: float
    misalignment_peak_after_change_db: float


def run_aec_experiment(fs: int = 8000, duration_s: float = 20.0,
                       change_s: float = 10.0, seed: int = 7) -> AECResult:
    """Simulate far-end speech, two echo paths, an abrupt switch and NLMS."""
    from scipy.signal import lfilter

    rng = np.random.default_rng(seed)
    n = int(duration_s * fs)
    change_sample = int(change_s * fs)

    # Speech-like coloured far-end signal with slow amplitude modulation.
    white = rng.standard_normal(n)
    far = lfilter([1.0], [1.0, -0.8], white)
    t = np.arange(n) / fs
    amp = 0.6 + 0.4 * np.abs(np.sin(2 * np.pi * 1.5 * t + rng.uniform(0, 6)))
    far = far * amp

    ir1 = make_echo_path(fs, seed=11, delay_ms=20.0)
    ir2 = make_echo_path(fs, seed=23, delay_ms=30.0)
    echo = np.concatenate([
        lfilter(ir1, [1.0], far[:change_sample]),
        lfilter(ir2, [1.0], far[change_sample:]),
    ])
    # Low-level near-end sensor noise.
    mic = echo + 0.01 * rng.standard_normal(n) * np.std(echo)

    far = far / (np.std(far) + 1e-9)

    nlms = NLMS(taps=320, mu=1.0)
    error = np.zeros(n)
    misalign = np.zeros(n)
    active_ir = ir1
    for i in range(n):
        if i == change_sample:
            active_ir = ir2
        err, _ = nlms.step(far[i], mic[i])
        error[i] = err
        # Misalignment against the true (padded) echo path.
        true = np.zeros(nlms.taps)
        take = min(nlms.taps, len(active_ir))
        true[:take] = active_ir[:take]
        diff = nlms.weights - true
        misalign[i] = 10.0 * np.log10(
            (np.sum(diff ** 2) + 1e-12) / (np.sum(true ** 2) + 1e-12)
        )

    erle_curve = block_erle(mic, error, fs)
    block = int(0.04 * fs)
    block_times = (np.arange(len(erle_curve)) + 0.5) * block / fs
    pre_mask = (block_times > 4.0) & (block_times < change_s - 0.5)
    transient_mask = (block_times > change_s) & (block_times < change_s + 1.0)
    post_mask = block_times > (duration_s - 3.0)

    # Re-convergence time: first block after change where ERLE recovers to
    # within 5 dB of the pre-change mean and stays above it for 0.5 s.
    target = erle_curve[pre_mask].mean() - 5.0
    hold = int(0.5 / 0.04)
    reconverge = np.nan
    after_idx = np.where(block_times > change_s)[0]
    for k in after_idx:
        if k + hold < len(erle_curve) and np.all(erle_curve[k:k + hold] >= target):
            reconverge = block_times[k] - change_s
            break

    return AECResult(
        erle_curve=erle_curve,
        erle_pre=float(erle_curve[pre_mask].mean()),
        erle_transient=float(erle_curve[transient_mask].mean()),
        erle_reconverged=float(erle_curve[post_mask].mean()),
        misalignment_curve=misalign,
        reconverge_time_s=float(reconverge),
        change_sample=change_sample,
        misalignment_converged_db=float(misalign[-int(2 * fs):].mean()),
        misalignment_peak_after_change_db=float(
            misalign[change_sample:change_sample + fs].max()),
    )
