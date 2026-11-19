"""ROC / threshold / confidence-distribution / separation metrics."""

from __future__ import annotations

import numpy as np


def roc_curve(pos_scores: np.ndarray, neg_scores: np.ndarray,
              n_points: int = 201):
    """Scores are confidences (larger = more keyword-like)."""
    grid = np.linspace(0.0, 1.0, n_points)
    tpr, fpr = [], []
    for th in grid:
        tpr.append(np.mean(pos_scores >= th))
        fpr.append(np.mean(neg_scores >= th))
    return grid, np.array(tpr), np.array(fpr)


def auc(tpr: np.ndarray, fpr: np.ndarray) -> float:
    order = np.argsort(fpr)
    return float(np.trapezoid(tpr[order], fpr[order]))


def eer(tpr: np.ndarray, fpr: np.ndarray) -> tuple[float, float]:
    """Equal-error-rate operating point (FAR = 1 - TPR)."""
    frr = 1.0 - tpr
    idx = int(np.argmin(np.abs(frr - fpr)))
    return float((frr[idx] + fpr[idx]) / 2.0), idx


def threshold_at_far(pos_dist: np.ndarray, neg_dist: np.ndarray,
                     target_far: float = 0.01) -> tuple[float, float]:
    """Pick a distance threshold on dev negatives so FAR <= target_far.

    Distances fire when distance <= threshold.
    """
    neg_sorted = np.sort(neg_dist)
    k = max(0, int(np.floor(target_far * len(neg_sorted))) - 1)
    th = float(neg_sorted[k])
    far = float(np.mean(neg_dist <= th))
    tpr = float(np.mean(pos_dist <= th))
    return th, tpr


def d_prime(pos: np.ndarray, neg: np.ndarray) -> float:
    """Sensitivity index d' using confidence distributions."""
    pooled = np.sqrt((pos.var(ddof=1) + neg.var(ddof=1)) / 2.0)
    return float((pos.mean() - neg.mean()) / (pooled + 1e-12))


def fisher_ratio(pos: np.ndarray, neg: np.ndarray) -> float:
    between = (pos.mean() - neg.mean()) ** 2
    within = pos.var(ddof=1) + neg.var(ddof=1)
    return float(between / (within + 1e-12))


def confidence_summary(pos_conf: np.ndarray,
                       neg_conf: np.ndarray) -> dict[str, float]:
    return {
        "pos_mean": float(pos_conf.mean()),
        "pos_std": float(pos_conf.std(ddof=1)),
        "pos_p05": float(np.percentile(pos_conf, 5)),
        "neg_mean": float(neg_conf.mean()),
        "neg_std": float(neg_conf.std(ddof=1)),
        "neg_p95": float(np.percentile(neg_conf, 95)),
        "d_prime": d_prime(pos_conf, neg_conf),
        "fisher_ratio": fisher_ratio(pos_conf, neg_conf),
    }
