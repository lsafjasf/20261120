"""Generate all evaluation figures."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from .aec import run_aec_experiment


def _load(metrics_path: Path) -> dict:
    with open(metrics_path, encoding="utf-8") as fh:
        return json.load(fh)


def plot_roc(results: dict, out: Path):
    fig, ax = plt.subplots(figsize=(7, 6))
    for kind, kw in results["keywords"].items():
        roc = kw["roc"]
        ax.plot(roc["fpr"], roc["tpr"], linewidth=2,
                label=(f"keyword_{'AB'[int(kind)]}: "
                       f"AUC={roc['auc']:.3f}, EER={roc['eer']:.3f}"))
    ax.plot([0, 1], [0, 1], "k--", linewidth=1, alpha=0.5)
    ax.set_xlabel("False Acceptance Rate")
    ax.set_ylabel("True Positive Rate")
    ax.set_title("ROC Curves of the Two DTW Keyword Detectors")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out / "fig1_roc.png", dpi=130)
    plt.close(fig)


def plot_confidence(results: dict, out: Path):
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5), sharey=True)
    for kind, kw in results["keywords"].items():
        ax = axes[int(kind)]
        raw = kw["confidence_raw"]
        bins = np.linspace(0, 1, 32)
        ax.hist(raw["pos"], bins=bins, alpha=0.65, density=True,
                label="own keyword", color="#2a9d8f")
        ax.hist(raw["neg"], bins=bins, alpha=0.65, density=True,
                label="negatives (OOV + noise)", color="#e76f51")
        th = results["calibration"][kind]["confidence_threshold"]
        ax.axvline(th, color="black", linestyle="--", linewidth=1.5,
                   label=f"threshold {th:.3f}")
        ax.set_title(f"keyword_{'AB'[int(kind)]} confidence distribution")
        ax.set_xlabel("confidence")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=9)
    axes[0].set_ylabel("density")
    fig.tight_layout()
    fig.savefig(out / "fig2_confidence_distributions.png", dpi=130)
    plt.close(fig)


def plot_robustness(results: dict, out: Path):
    conditions = ["clean", "snr20", "snr10", "snr5", "snr0",
                  "speed0.9", "speed1.1"]
    labels = ["clean", "SNR20", "SNR10", "SNR5", "SNR0", "0.9x", "1.1x"]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.5))
    x = np.arange(len(conditions))
    width = 0.35
    for kind, kw in results["keywords"].items():
        offset = -width / 2 if kind == "0" else width / 2
        recall = [kw["by_condition"][c]["gated_recall"] for c in conditions]
        dprime = [kw["by_condition"][c]["d_prime"] for c in conditions]
        ax1.bar(x + offset, recall, width, label=f"keyword_{'AB'[int(kind)]}")
        ax2.plot(x, dprime, marker="o",
                 label=f"keyword_{'AB'[int(kind)]}")
    ax1.set_xticks(x)
    ax1.set_xticklabels(labels)
    ax1.set_ylabel("gated recall")
    ax1.set_title("Recall under Noise and Speed Change")
    ax1.set_ylim(0, 1.08)
    ax1.grid(alpha=0.3, axis="y")
    ax1.legend()
    ax2.set_xticks(x)
    ax2.set_xticklabels(labels)
    ax2.set_ylabel("d' (own vs negatives)")
    ax2.set_title("Separation under Noise and Speed Change")
    ax2.grid(alpha=0.3)
    ax2.legend()
    fig.tight_layout()
    fig.savefig(out / "fig3_robustness.png", dpi=130)
    plt.close(fig)


def plot_cross_trigger(results: dict, out: Path):
    fig, ax = plt.subplots(figsize=(8, 4.5))
    names = list(results["cross_trigger"].keys())
    rates = [results["cross_trigger"][n]["cross_trigger_rate"] for n in names]
    margins = [results["cross_trigger"][n]["margin_p95"] for n in names]
    x = np.arange(len(names))
    bars = ax.bar(x, rates, color=["#457b9d", "#1d3557"], width=0.5)
    ax.set_xticks(x)
    ax.set_xticklabels(names, rotation=10)
    ax.set_ylabel("cross-trigger rate")
    ax.set_title("Cross-Keyword False Triggering (target: zero)")
    ax.set_ylim(0, max(0.02, max(rates) * 1.5))
    for bar, m in zip(bars, margins):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                f"{bar.get_height():.3f}\n95p margin={m:.3f}",
                ha="center", va="bottom", fontsize=9)
    ax.grid(alpha=0.3, axis="y")
    fig.tight_layout()
    fig.savefig(out / "fig4_cross_trigger.png", dpi=130)
    plt.close(fig)


def plot_separation(results: dict, out: Path):
    fig, ax = plt.subplots(figsize=(8.5, 4.8))
    conds = ["clean", "snr20", "snr10", "snr5", "snr0"]
    labels = ["clean", "SNR20", "SNR10", "SNR5", "SNR0"]
    x = np.arange(len(conds))
    width = 0.35
    for kind, name in ((0, "keyword_A"), (1, "keyword_B")):
        per = results["separation_under_noise"][name]["per_condition"]
        vals = [per[c]["d_prime"] for c in conds]
        offset = -width / 2 if kind == 0 else width / 2
        ax.bar(x + offset, vals, width, label=name)
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("d' between the two keywords")
    ax.set_title("Inter-Keyword Separation under Noise")
    ax.grid(alpha=0.3, axis="y")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out / "fig5_separation_under_noise.png", dpi=130)
    plt.close(fig)


def plot_aec(out: Path, fs: int = 8000):
    result = run_aec_experiment(fs=fs)
    block = 0.04
    erle_t = (np.arange(len(result.erle_curve)) + 0.5) * block
    mis_t = np.arange(len(result.misalignment_curve)) / fs
    change_s = result.change_sample / fs

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 7), sharex=True)
    ax1.plot(erle_t, result.erle_curve, color="#264653", linewidth=1.2)
    ax1.axvline(change_s, color="red", linestyle="--",
                label="abrupt echo-path change")
    ax1.axhline(result.erle_pre, color="#2a9d8f", linestyle=":",
                label=f"pre-change ERLE {result.erle_pre:.1f} dB")
    ax1.axhline(result.erle_reconverged, color="#e9c46a", linestyle=":",
                label=f"re-converged ERLE {result.erle_reconverged:.1f} dB")
    ax1.set_ylabel("ERLE (dB)")
    ax1.set_title("NLMS Echo Cancellation: ERLE and Coefficient Misalignment")
    ax1.grid(alpha=0.3)
    ax1.legend(loc="lower right", fontsize=9)

    ax2.plot(mis_t, result.misalignment_curve, color="#6a4c93", linewidth=1.0)
    ax2.axvline(change_s, color="red", linestyle="--")
    ax2.set_ylabel("normalised misalignment (dB)")
    ax2.set_xlabel("time (s)")
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out / "fig6_aec_erle.png", dpi=130)
    plt.close(fig)
    return result


def make_all(metrics_path: str | Path, out: str | Path):
    metrics_path, out = Path(metrics_path), Path(out)
    results = _load(metrics_path)
    plot_roc(results, out)
    plot_confidence(results, out)
    plot_robustness(results, out)
    plot_cross_trigger(results, out)
    plot_separation(results, out)
    aec = plot_aec(out)
    return aec
