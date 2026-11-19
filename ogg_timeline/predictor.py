"""A deterministic, buffer-aware stall-risk threshold calculator."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class PredictorConfig:
    """Two-tier predictor thresholds in milliseconds.

    ``low_buffer_threshold_ms`` is deliberately larger than the normal
    threshold.  A larger stop/recovery margin causes the low-buffer tier to
    predict earlier, which is the conservative direction for preventing an
    audible underrun.
    """

    normal_threshold_ms: float = 120.0
    low_buffer_threshold_ms: float = 240.0
    low_buffer_watermark_ms: float = 300.0

    def __post_init__(self) -> None:
        if self.normal_threshold_ms <= 0:
            raise ValueError("normal threshold must be positive")
        if self.low_buffer_threshold_ms < self.normal_threshold_ms:
            raise ValueError(
                "low-buffer threshold must be at least the normal threshold"
            )
        if self.low_buffer_watermark_ms <= 0:
            raise ValueError("low-buffer watermark must be positive")

    def threshold_for(self, buffer_ms: float) -> tuple[float, str]:
        if buffer_ms < 0:
            buffer_ms = 0.0
        if buffer_ms < self.low_buffer_watermark_ms:
            return self.low_buffer_threshold_ms, "low_buffer"
        return self.normal_threshold_ms, "normal"

    @property
    def conservative_at_low_buffer(self) -> bool:
        return self.low_buffer_threshold_ms > self.normal_threshold_ms

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["conservative_at_low_buffer"] = self.conservative_at_low_buffer
        value["prediction_threshold_ms"] = self.low_buffer_threshold_ms
        return value


@dataclass(frozen=True)
class StallPrediction:
    buffer_ms: float
    threshold_ms: float
    tier: str
    predicted_stall: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def predict_stall(
    buffer_ms: float, config: PredictorConfig | None = None
) -> StallPrediction:
    """Predict an imminent underrun using the tier's safety threshold."""

    config = config or PredictorConfig()
    threshold, tier = config.threshold_for(buffer_ms)
    return StallPrediction(
        buffer_ms=float(buffer_ms),
        threshold_ms=threshold,
        tier=tier,
        predicted_stall=float(buffer_ms) < threshold,
    )
