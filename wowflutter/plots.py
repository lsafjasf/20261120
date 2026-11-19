"""Diagnostic figures for the wow/flutter experiment."""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def _shade_gaps(ax, gaps, color="red"):
    for a, b in gaps:
        ax.axvspan(a, b, color=color, alpha=0.15, lw=0)


def plot_speed(path, t, truth_dev, est_dev, gaps):
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(t, truth_dev * 100, label="true speed deviation", lw=1.2)
    ax.plot(t, est_dev * 100, label="estimated (from pilot)", lw=0.8,
            alpha=0.8)
    _shade_gaps(ax, gaps)
    ax.set(xlabel="time (s)", ylabel="speed deviation (%)",
           title="Pilot-derived speed curve vs ground truth")
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def plot_deviation(path, t, dev_before, dev_after, gaps):
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(t, dev_before, label="before correction", lw=0.8)
    ax.plot(t, dev_after, label="after correction", lw=0.8)
    _shade_gaps(ax, gaps)
    ax.set(xlabel="time (s)", ylabel="pilot freq deviation (%)",
           title="Residual frequency deviation of the pilot tone")
    ax.set_ylim(-1, 1)
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def plot_gap_waveform(path, fs, clean, corrected, bypassed, gap,
                     span_ms=40.0):
    t0 = gap[0] - span_ms / 2000.0
    t1 = gap[0] + span_ms / 2000.0
    i0, i1 = int(t0 * fs), int(t1 * fs)
    tt = np.arange(i0, i1) / fs
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(tt, clean[i0:i1], label="clean source", lw=1.2)
    ax.plot(tt, corrected[i0:i1], label="corrected", lw=0.9, alpha=0.85)
    ax.plot(tt, bypassed[i0:i1], label="bypassed (reviewer)", lw=0.9,
            alpha=0.7)
    ax.axvline(gap[0], color="red", ls="--", lw=0.8)
    ax.set(xlabel="time (s)", ylabel="amplitude",
           title="Waveform continuity at estimated-interval boundary "
                 f"({gap[0]:.2f} s)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def plot_lr_lag(path, times, lags_before, lags_after, truth_t, truth_lag):
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(truth_t, truth_lag * 1e3, label="true R-L skew", lw=1.2)
    ax.plot(times, lags_before * 1e3, ".", ms=3, label="measured before")
    ax.plot(times, lags_after * 1e3, ".", ms=3, label="measured after")
    ax.set(xlabel="time (s)", ylabel="L/R lag (ms)",
           title="Inter-channel alignment (shared map cannot remove skew)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
