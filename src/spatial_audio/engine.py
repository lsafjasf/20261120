"""Event-to-audio render pipeline with latency instrumentation.

Orientation events are produced on the sensor thread and consumed at fixed
audio-render ticks through a FIFO queue, mirroring a real browser pipeline
(``deviceorientation`` -> audio parameter automation). Every applied event
is timestamped both at sensor capture and at render application so the
end-to-end update latency can be measured.
"""

import math
from collections import deque
from dataclasses import dataclass

from .filters import angle_diff, wrap_angle
from .spatial import (
    SlewLimiter,
    doppler_ratio,
    equal_power_gains,
    ratio_to_cents,
    relative_azimuth,
)


@dataclass(frozen=True)
class OrientationEvent:
    """Re-exported event type (see :mod:`orientation` for the simulator)."""

    t: float
    yaw: float
    seq: int


@dataclass(frozen=True)
class RenderState:
    """All audio parameters emitted for one render tick."""

    t: float
    raw_yaw: float
    filtered_yaw: float
    angular_velocity: float
    azimuth: float
    gain_l: float
    gain_r: float
    doppler_target: float
    doppler_applied: float
    doppler_cents: float
    queue_depth: int
    applied_seq: int | None


class SpatialEngine:
    """Consumes orientation events and produces per-tick render states."""

    def __init__(
        self,
        angle_filter,
        source_yaw: float = 0.0,
        source_distance: float = 1.0,
        render_hz: float = 120.0,
        doppler_slew_rate: float = 1.5,
        doppler_slew_enabled: bool = True,
    ) -> None:
        self.filter = angle_filter
        self.source_yaw = source_yaw
        self.source_distance = source_distance
        self.render_dt = 1.0 / render_hz
        self.slew_enabled = doppler_slew_enabled
        self._slew = SlewLimiter(doppler_slew_rate, initial=1.0)
        self._queue: deque[OrientationEvent] = deque()
        self._t_prev = None
        self._yaw_prev = None
        self._raw_yaw = 0.0
        self.latency_samples: list[tuple[float, float, int]] = []

    def push_event(self, event: OrientationEvent) -> None:
        """Enqueue a sensor event (sensor timestamp ``event.t``)."""
        self._queue.append(event)

    def render(self, now: float) -> RenderState:
        """Apply every queued event due by ``now`` and render one tick."""
        applied_seq = None
        filtered_yaw = self._yaw_prev if self._yaw_prev is not None else 0.0

        while self._queue and self._queue[0].t <= now:
            event = self._queue.popleft()
            self._raw_yaw = event.yaw
            filtered_yaw = self.filter(now, event.yaw)
            applied_seq = event.seq
            self.latency_samples.append((event.t, now, event.seq))

        if self._t_prev is None:
            velocity = 0.0
        else:
            dt = max(now - self._t_prev, 1e-4)
            velocity = (
                angle_diff(filtered_yaw, self._yaw_prev) / dt
                if self._yaw_prev is not None
                else 0.0
            )

        azimuth = relative_azimuth(self.source_yaw, filtered_yaw)
        gain_l, gain_r = equal_power_gains(azimuth)
        target = doppler_ratio(velocity, self.source_distance, azimuth)
        if self.slew_enabled:
            applied = self._slew.update(now, target)
        else:
            applied = target
            self._slew.value = target

        self._t_prev = now
        self._yaw_prev = filtered_yaw

        return RenderState(
            t=now,
            raw_yaw=wrap_angle(self._raw_yaw),
            filtered_yaw=wrap_angle(filtered_yaw),
            angular_velocity=velocity,
            azimuth=azimuth,
            gain_l=gain_l,
            gain_r=gain_r,
            doppler_target=target,
            doppler_applied=applied,
            doppler_cents=ratio_to_cents(applied),
            queue_depth=len(self._queue),
            applied_seq=applied_seq,
        )

    def drain(self, duration_s: float):
        """Run render ticks from 0 to ``duration_s``; return state list."""
        states = []
        n = int(round(duration_s / self.render_dt))
        for i in range(n + 1):
            states.append(self.render(i * self.render_dt))
        return states

    def latency_statistics(self) -> dict[str, float]:
        """End-to-end (sensor capture -> render apply) latency in ms."""
        if not self.latency_samples:
            return {}
        values = sorted(
            (applied - captured) * 1000.0
            for captured, applied, _ in self.latency_samples
        )

        def pct(p: float) -> float:
            idx = min(len(values) - 1, int(p * (len(values) - 1)))
            return values[idx]

        return {
            "n": float(len(values)),
            "mean_ms": sum(values) / len(values),
            "p50_ms": pct(0.50),
            "p95_ms": pct(0.95),
            "p99_ms": pct(0.99),
            "max_ms": values[-1],
            "min_ms": values[0],
        }
