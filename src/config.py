"""Fusion configuration shared by the scoring pipeline and report."""
from __future__ import annotations

CONFIG = {
    "width": 640,
    "height": 360,
    "fps": 24,
    "clipSeconds": 2,
    "motionScale": 0.055,
    "motionWeightBeta": 1.0,
    "scoreGamma": 4.0,
    "weights": {"fidelity": 0.45, "detail": 0.35, "motion": 0.20},
    "fidelityMix": {"lumaSsim": 0.50, "chromaSsim": 0.20, "psnr": 0.30},
    "detailMix": {"gradientEnergy": 0.55, "gradientCorrelation": 0.45},
    "psnrFloorDb": 18.0,
    "psnrCeilDb": 42.0,
    "psnrSentinelDb": 99.0,
    "lowFusedScore": 36.0,
    "lowSubjectiveMos": 3.0,
    "significantRankDelta": 2,
}

# Band boundaries are inclusive on the lower edge: [85,100], [70,85), ...
BANDS = [
    ("excellent", 85.0, 100.0),
    ("good", 65.0, 85.0),
    ("fair", 50.0, 65.0),
    ("poor", 35.0, 50.0),
    ("bad", 0.0, 35.0),
]


def band_for(score: float) -> str:
    for name, low, high in BANDS:
        if low <= score <= high or (low == 0.0 and score < high):
            return name
    return "bad"
