"""Discrete-event loss/retransmission model for first-frame timing.

Time unit: milliseconds.

A stream is split into MTU-sized packets. Every send attempt is lost with
probability `loss_rate` (independent Bernoulli draws from a seeded RNG so
chunk and whole-object runs share the same loss trace). A lost packet is
retransmitted after `rto_ms`; each retry re-enters the loss draw.

* Chunked mode: the first chunk holds the first frame; playout starts as
  soon as every packet of that chunk has arrived.
* Whole mode: the frame is unavailable until every packet of the complete
  object has arrived.
"""

import random
from dataclasses import dataclass


@dataclass
class ChunkConfig:
    object_bytes: int = 60_000
    mtu_bytes: int = 1_500
    chunk_bytes: int = 8_000
    rtt_ms: float = 40.0
    rto_ms: float = 120.0
    send_rate_mbps: float = 5.0

    @property
    def packet_send_ms(self):
        return self.mtu_bytes * 8 / self.send_rate_mbps / 1_000


def _packet_count(total_bytes, mtu):
    return max(1, (total_bytes + mtu - 1) // mtu)


def _deliver(needed_packets, cfg, loss_draws, rng, trial):
    """Return (first_frame_arrival_ms, attempts, lost_attempts).

    Pipelined sender with per-packet RTO timers (SACK-style selective
    retransmission): packet k is first offered at k * send_time, and a loss
    only delays that packet's own retry, never the packets behind it.
    First-frame arrival is the last successful arrival among the needed
    packets (a chunk/frame is playable once all of its packets are in).
    """
    send_time = cfg.packet_send_ms
    attempts = 0
    lost_attempts = 0
    arrival = {}
    pending = {}  # packet -> next retry time
    attempt_count = {}
    for packet in range(needed_packets):
        pending[packet] = packet * send_time
        attempt_count[packet] = 0
    while pending:
        packet = min(pending, key=lambda p: pending[p])
        send_at = pending.pop(packet)
        attempts += 1
        attempt_no = attempt_count[packet]
        attempt_count[packet] += 1
        key = (trial, packet, attempt_no)
        if key not in loss_draws:
            loss_draws[key] = rng.random() < cfg.loss_rate
        lost = loss_draws[key]
        if lost:
            lost_attempts += 1
            # RTO is measured from the send instant; retries keep their own
            # timer, so later packets continue to drain in parallel.
            pending[packet] = send_at + cfg.rto_ms
            continue
        arrival[packet] = send_at + cfg.rtt_ms / 2
    first_frame = max(arrival.values())
    return first_frame, attempts, lost_attempts


def simulate(loss_rate, trials=200, seed=20261120, **config_kwargs):
    cfg = ChunkConfig(**config_kwargs)
    cfg.loss_rate = loss_rate
    total_packets = _packet_count(cfg.object_bytes, cfg.mtu_bytes)
    first_chunk_packets = _packet_count(cfg.chunk_bytes, cfg.mtu_bytes)
    rng = random.Random(seed)
    # Shared loss trace: both modes index the same Bernoulli draw by
    # (trial, packet index, attempt number), so chunk vs whole differ only
    # in how many packets the first frame needs — never in luck.
    loss_draws = {}
    rows = []
    for trial in range(trials):
        chunk_arrival, _, chunk_losses = _deliver(
            first_chunk_packets, cfg, loss_draws, rng, trial)
        whole_arrival, _, whole_losses = _deliver(
            total_packets, cfg, loss_draws, rng, trial)
        rows.append({
            "trial": trial,
            "chunk_first_frame_ms": round(chunk_arrival, 3),
            "whole_first_frame_ms": round(whole_arrival, 3),
            "gain_ms": round(whole_arrival - chunk_arrival, 3),
            "chunk_loss_events": chunk_losses,
            "whole_loss_events": whole_losses,
            "chunk_earlier": chunk_arrival < whole_arrival,
        })
    return _summarize(rows, cfg, first_chunk_packets, total_packets)


def compare(loss_rates=(0.0, 0.02, 0.05, 0.10, 0.20), **kwargs):
    return [simulate(rate, **kwargs) for rate in loss_rates]


def _summarize(rows, cfg, chunk_packets, total_packets):
    def stats(key):
        values = sorted(r[key] for r in rows)
        return {
            "mean_ms": round(sum(values) / len(values), 3),
            "p50_ms": values[len(values) // 2],
            "p95_ms": values[int(len(values) * 0.95)],
            "max_ms": values[-1],
        }
    gains = [r["gain_ms"] for r in rows]
    earlier = sum(1 for r in rows if r["chunk_earlier"])
    return {
        "loss_rate": cfg.loss_rate,
        "trials": len(rows),
        "packets_first_chunk": chunk_packets,
        "packets_whole_object": total_packets,
        "chunk": stats("chunk_first_frame_ms"),
        "whole": stats("whole_first_frame_ms"),
        "mean_gain_ms": round(sum(gains) / len(gains), 3),
        "trials_chunk_earlier": earlier,
        "trials_tied": sum(1 for g in gains if g == 0),
        "chunk_always_earlier_or_tied": all(g >= 0 for g in gains),
        "rows": rows,
    }
