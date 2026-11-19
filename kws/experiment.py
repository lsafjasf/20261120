"""End-to-end keyword wake-up experiment."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from . import features as feats_mod
from . import metrics, synth
from .dataset import build_corpus
from .dtw import KeywordDetector, KeywordSpotter


class FeatureCache:
    def __init__(self, fs: int):
        self.fs = fs
        self._cache: dict[int, np.ndarray] = {}

    def get(self, sig: np.ndarray) -> np.ndarray:
        # Content hash: robust against Python object id reuse across runs.
        key = hash((sig.shape, sig.dtype.str, sig[:8].tobytes(),
                    sig[-8:].tobytes(), float(sig.sum())))
        if key not in self._cache:
            self._cache[key] = feats_mod.extract_features(sig, self.fs)
        return self._cache[key]


def _scores(detector: KeywordDetector, items, cache: FeatureCache):
    distances, confidences = [], []
    for item in items:
        sig = item[1] if isinstance(item, tuple) else item
        feat = cache.get(sig)
        distance = detector.raw_distance(feat)
        distances.append(distance)
        confidences.append(detector.confidence(distance))
    return np.array(distances), np.array(confidences)


def run(output_dir: str | Path = "outputs", seed: int = 20261120) -> dict:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed + 1)

    print("[1/5] Building corpus ...")
    corpus = build_corpus(seed=seed)
    cache = FeatureCache(synth.FS)

    print("[2/5] Enrolling templates (with noisy-template augmentation) ...")
    detectors = {}
    for kind in (0, 1):
        clean_features = [cache.get(sig) for sig in corpus.enroll[kind]]
        noisy_features = []
        for sig in corpus.enroll[kind][:15]:
            for snr in (20.0, 10.0, 5.0):
                noisy = synth.add_noise_at_snr(
                    sig, synth.generate_noise_segment(rng), snr, rng)
                noisy_features.append(cache.get(noisy))
        # Medoids are picked per cohort so noisy augmentations cannot
        # dominate the clean-template centroid.
        det = KeywordDetector(kind, synth.KEYWORD_DEFS[kind]["name"], band=0.3)
        det.enroll(clean_features, n_templates=4)
        noisy_picker = KeywordDetector(kind, "tmp", band=0.3)
        noisy_idx = noisy_picker.enroll(noisy_features, n_templates=4)
        det.templates += [noisy_features[k].copy() for k in noisy_idx]
        detectors[kind] = det

    print("[3/5] Calibrating thresholds on dev set ...")
    calibration = {}
    for kind in (0, 1):
        det = detectors[kind]
        dev_pos_dist, _ = _scores(det, corpus.dev_pos[kind], cache)
        dev_neg_dist, _ = _scores(det, corpus.dev_neg, cache)
        # First 60 dev negatives are OOV speech, the rest are noise only.
        dev_oov_dist = dev_neg_dist[:60]
        dev_noise_dist = dev_neg_dist[60:]
        det.calibrate(dev_neg_dist)
        threshold, dev_tpr = metrics.threshold_at_far(
            dev_pos_dist, dev_oov_dist, target_far=0.30)
        det.set_threshold_by_distance(threshold)
        calibration[kind] = {
            "distance_threshold": threshold,
            "confidence_threshold": det.confidence_threshold(),
            "dev_tpr_at_30pct_oov_far": dev_tpr,
            "dev_oov_far": float(np.mean(dev_oov_dist <= threshold)),
            "dev_noise_far": float(np.mean(dev_noise_dist <= threshold)),
        }
    margin = 0.05
    spotter = KeywordSpotter(detectors, margin=margin)
    calibration["margin_gate"] = margin

    print("[4/5] Scoring evaluation sets (clean / noise / speed / negatives) ...")
    results: dict = {"calibration": calibration, "keywords": {}}

    for kind in (0, 1):
        det = detectors[kind]
        pos_dist, pos_conf = _scores(det, corpus.test_pos[kind], cache)
        neg_dist, neg_conf = _scores(det, corpus.test_neg, cache)

        grid, tpr, fpr = metrics.roc_curve(pos_conf, neg_conf)
        eer_val, eer_idx = metrics.eer(tpr, fpr)
        kw_result = {
            "roc": {"grid": grid.tolist(), "tpr": tpr.tolist(),
                    "fpr": fpr.tolist(), "auc": metrics.auc(tpr, fpr),
                    "eer": eer_val,
                    "eer_confidence_threshold": float(grid[eer_idx])},
            "confidence_overall": metrics.confidence_summary(pos_conf, neg_conf),
            "by_condition": {},
            "confidence_raw": {
                "pos": pos_conf.tolist(), "neg": neg_conf.tolist(),
                "pos_distance": pos_dist.tolist(),
                "neg_distance": neg_dist.tolist(),
                "pos_condition": [item[0] for item in corpus.test_pos[kind]],
                "neg_condition": [item[0] for item in corpus.test_neg],
            },
        }

        labels = np.array([item[0] for item in corpus.test_pos[kind]])
        for cond in sorted(set(labels)):
            mask = labels == cond
            sub = metrics.confidence_summary(
                pos_conf[mask], neg_conf)
            sub["recall"] = float(np.mean(pos_dist[mask] <= det.threshold))
            gated_hits = 0
            for idx in np.where(mask)[0]:
                label, _ = spotter.decide(
                    cache.get(corpus.test_pos[kind][idx][1]))
                if label == kind:
                    gated_hits += 1
            sub["gated_recall"] = gated_hits / int(mask.sum())
            sub["n"] = int(mask.sum())
            kw_result["by_condition"][cond] = sub

        neg_rejected = 0
        for _, sig in corpus.test_neg:
            label, _ = spotter.decide(cache.get(sig))
            if label != kind:
                neg_rejected += 1
        kw_result["negative_rejection"] = {
            "n_negatives": len(corpus.test_neg),
            "rejection_rate": neg_rejected / len(corpus.test_neg),
        }

        # FAR on long negative pseudo-events (false wake-ups).
        far_dist, far_conf = _scores(det, corpus.far_events, cache)
        far_decisions = []
        far_margins = []
        for sig in corpus.far_events:
            feat = cache.get(sig)
            label, info = spotter.decide(feat)
            far_decisions.append(label)
            far_margins.append(info["margin"])
        n_false = sum(1 for label in far_decisions if label == kind)
        kw_result["false_wake"] = {
            "n_negative_events": len(corpus.far_events),
            "false_wake_ups": n_false,
            "false_wake_rate": n_false / len(corpus.far_events),
            "margin_mean": float(np.mean(far_margins)),
            "margin_p95": float(np.percentile(far_margins, 95)),
        }
        results["keywords"][kind] = kw_result

    print("[5/5] Cross-trigger and noise-separation analysis ...")
    cross = {}
    for kind in (0, 1):
        other = 1 - kind
        det = detectors[kind]
        other_dist, other_conf = _scores(det, corpus.test_pos[other], cache)
        fired = 0
        margins = []
        for _, sig in corpus.test_pos[other]:
            label, info = spotter.decide(cache.get(sig))
            margins.append(info["margin"])
            if label == kind:
                fired += 1
        cross[f"{det.name}_on_{detectors[other].name}"] = {
            "cross_triggers": fired,
            "n_other_keyword_events": len(other_dist),
            "cross_trigger_rate": fired / len(other_dist),
            "confidence_mean": float(other_conf.mean()),
            "confidence_max": float(other_conf.max()),
            "margin_mean": float(np.mean(margins)),
            "margin_p95": float(np.percentile(margins, 95)),
            "own_confidence_threshold": det.confidence_threshold(),
        }
    results["cross_trigger"] = cross

    separation = {}
    for kind in (0, 1):
        det = detectors[kind]
        own = _scores(det, corpus.test_pos[kind], cache)[1]
        other = _scores(det, corpus.test_pos[1 - kind], cache)[1]
        labels = np.array([item[0] for item in corpus.test_pos[kind]])
        per_snr = {}
        for cond in ("clean", "snr20", "snr10", "snr5", "snr0"):
            mask = labels == cond
            if mask.sum():
                per_snr[cond] = {
                    "d_prime": metrics.d_prime(own[mask], other),
                    "fisher_ratio": metrics.fisher_ratio(own[mask], other),
                }
        separation[det.name] = {
            "per_condition": per_snr,
            "d_prime_all": metrics.d_prime(own, other),
            "fisher_all": metrics.fisher_ratio(own, other),
        }
    results["separation_under_noise"] = separation

    with open(output_dir / "metrics.json", "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2, ensure_ascii=False)
    return results
