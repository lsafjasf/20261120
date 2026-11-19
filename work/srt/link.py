"""Lossy, delayed, jittered duplex link used by the simulated endpoints."""
from __future__ import annotations

import heapq
import random

from .packets import Packet


class Link:
    """One direction of a network path.

    Packets are dropped with probability `loss` and delivered after
    base latency + uniform jitter. Delivery events sit in a min-heap
    keyed by delivery time so the simulation is a discrete-event loop.
    """

    def __init__(self, latency_ms: float, jitter_ms: float, loss: float,
                 rng: random.Random):
        self.latency_ms = latency_ms
        self.jitter_ms = jitter_ms
        self.loss = loss
        self.rng = rng
        self._queue: list = []
        self._ctr = 0
        self.sent = 0
        self.dropped = 0
        self.blackout_until = 0.0
        self.blackout_dropped = 0

    def send(self, pkt: Packet, now_ms: float) -> None:
        self.sent += 1
        if now_ms < self.blackout_until:
            self.blackout_dropped += 1
            return
        if self.rng.random() < self.loss:
            self.dropped += 1
            return
        delay = self.latency_ms + self.rng.uniform(0, self.jitter_ms)
        pkt.sent_at = now_ms
        self._ctr += 1
        heapq.heappush(self._queue, (now_ms + delay, self._ctr, pkt))

    def next_delivery(self) -> float | None:
        return self._queue[0][0] if self._queue else None

    def poll(self, now_ms: float) -> list[Packet]:
        out = []
        while self._queue and self._queue[0][0] <= now_ms:
            out.append(heapq.heappop(self._queue)[2])
        return out

    @property
    def loss_rate(self) -> float:
        # Blackout drops model an outage, not steady-state channel loss.
        normal = self.sent - self.blackout_dropped
        return self.dropped / normal if normal else 0.0


class DuplexLink:
    """Two opposite Link directions forming a full-duplex path A<->B."""

    def __init__(self, latency_ms: float, jitter_ms: float, loss: float,
                 seed: int = 42):
        rng = random.Random(seed)
        self.a2b = Link(latency_ms, jitter_ms, loss, rng)
        self.b2a = Link(latency_ms, jitter_ms, loss, rng)

    def direction(self, src: str) -> Link:
        return self.a2b if src == "A" else self.b2a

    def next_delivery(self) -> float | None:
        times = [t for t in (self.a2b.next_delivery(),
                             self.b2a.next_delivery()) if t is not None]
        return min(times) if times else None
