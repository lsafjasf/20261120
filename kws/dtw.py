"""Banded dynamic time warping and the keyword-spotting detector."""

from __future__ import annotations

import numpy as np


def _frame_distance(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Cosine distance matrix (robust to per-frame energy shifts)."""
    a_norm = a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-9)
    b_norm = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-9)
    similarity = a_norm @ b_norm.T
    return np.nan_to_num(1.0 - similarity, nan=1e6, posinf=1e6)


def dtw_distance(a: np.ndarray, b: np.ndarray, band: float = 0.3) -> float:
    """DTW path cost with a Sakoe-Chiba band, normalised by path length."""
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        return 1e6
    width = max(2, int(np.ceil(band * max(n, m))), abs(n - m))
    cost = _frame_distance(a, b)
    inf = 1e12
    acc = np.full((n + 1, m + 1), inf, dtype=np.float64)
    acc[0, 0] = 0.0
    for i in range(1, n + 1):
        center = i * m / n
        lo = max(1, center - width)
        hi = min(m, center + width)
        lo, hi = int(lo), int(hi)
        for j in range(lo, hi + 1):
            up = acc[i - 1, j]
            left = acc[i, j - 1] if j > lo else inf
            diag = acc[i - 1, j - 1]
            best = min(up, left, diag)
            acc[i, j] = cost[i - 1, j - 1] + best
    if not np.isfinite(acc[n, m]):
        return float(np.nanmean(cost) * max(n, m)) / max(n, m)
    return float(acc[n, m]) / (n + m)


class KeywordDetector:
    """One detector per keyword: DTW templates, threshold and calibration."""

    def __init__(self, keyword_id: int, name: str, band: float = 0.12):
        self.keyword_id = keyword_id
        self.name = name
        self.band = band
        self.templates: list[np.ndarray] = []
        self.threshold: float | None = None
        self.cal_mean: float = 0.0
        self.cal_scale: float = 1.0

    def enroll(self, features_list: list[np.ndarray], n_templates: int = 3):
        """Pick medoid templates (minimal mean DTW distance to the cohort)."""
        n = len(features_list)
        dmat = np.zeros((n, n))
        for i in range(n):
            for j in range(i + 1, n):
                d = dtw_distance(features_list[i], features_list[j], self.band)
                dmat[i, j] = dmat[j, i] = d
        medoids = np.argsort(dmat.mean(axis=1))[:n_templates]
        self.templates = [features_list[k].copy() for k in medoids]
        return medoids

    def raw_distance(self, feat: np.ndarray) -> float:
        dists = [dtw_distance(feat, t, self.band) for t in self.templates]
        return float(np.min(dists))

    def calibrate(self, distances: np.ndarray):
        """Fit confidence mapping from negative-sample distance statistics."""
        self.cal_mean = float(np.mean(distances))
        self.cal_scale = float(np.std(distances) + 1e-9)

    def confidence(self, distance: float) -> float:
        """Map distance to a (0, 1) confidence; higher is more keyword-like."""
        z = (self.cal_mean - distance) / self.cal_scale
        return float(1.0 / (1.0 + np.exp(-z)))

    def set_threshold_by_distance(self, distance_threshold: float):
        self.threshold = float(distance_threshold)

    def confidence_threshold(self) -> float:
        assert self.threshold is not None
        return self.confidence(self.threshold)

    def detect(self, feat: np.ndarray) -> tuple[bool, float, float]:
        distance = self.raw_distance(feat)
        conf = self.confidence(distance)
        fired = self.threshold is not None and distance <= self.threshold
        return fired, distance, conf


class KeywordSpotter:
    """Two-keyword spotter with confidence-margin gating.

    A wake-up for keyword k requires k's distance threshold to be passed and
    its confidence to exceed the competing keyword's confidence by a margin.
    """

    def __init__(self, detectors: dict[int, KeywordDetector],
                 margin: float = 0.05):
        self.detectors = detectors
        self.margin = margin

    def score(self, feat: np.ndarray) -> dict[int, dict[str, float]]:
        out = {}
        for kind, det in self.detectors.items():
            distance = det.raw_distance(feat)
            out[kind] = {
                "distance": distance,
                "confidence": det.confidence(distance),
            }
        return out

    def decide(self, feat: np.ndarray) -> tuple[int | None, dict]:
        scores = self.score(feat)
        winner = min(scores, key=lambda k: scores[k]["distance"])
        loser = 1 - winner
        # Distance gap: a true keyword is clearly closer to its own template
        # than to the competing keyword's templates.
        margin = scores[loser]["distance"] - scores[winner]["distance"]
        det = self.detectors[winner]
        threshold_ok = (det.threshold is not None
                        and scores[winner]["distance"] <= det.threshold)
        fired = threshold_ok and margin >= self.margin
        return (winner if fired else None), {"scores": scores, "margin": margin}
