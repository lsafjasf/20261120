"""Deterministic application-chunk transfer simulation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from typing import Any


@dataclass(frozen=True)
class NetworkConfig:
    bandwidth_bps: int = 2_000_000
    rtt_ms: float = 60.0
    response_header_bytes: int = 200
    packet_payload_bytes: int = 1200
    packet_header_bytes: int = 40
    chunk_header_bytes: int = 24
    loss_percent: float = 0.0
    loss_seed: int = 20261120
    max_retries: int = 8

    @property
    def bytes_per_second(self) -> float:
        return self.bandwidth_bps / 8.0


@dataclass(frozen=True)
class _Packet:
    media_start: int
    media_end: int
    delivery_s: float


@dataclass
class TransferResult:
    chunked: bool
    config: NetworkConfig
    chunks: list[dict[str, Any]]
    prefix_ready: list[tuple[int, float]]
    lost_initial_packets: int
    retransmissions: int
    packet_count: int
    retransmitted_payload_bytes: int
    wire_bytes: int

    def ready_at(self, media_offset: int) -> float:
        """Time at which every byte in ``[0, media_offset]`` is available."""
        if media_offset < 0:
            return self.config.rtt_ms / 2000.0 + (
                self.config.response_header_bytes / self.config.bytes_per_second
            )
        lo, hi = 0, len(self.prefix_ready)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.prefix_ready[mid][0] < media_offset:
                lo = mid + 1
            else:
                hi = mid
        if lo == len(self.prefix_ready):
            raise ValueError(f"offset {media_offset} is outside the transfer")
        return self.prefix_ready[lo][1]

    def chunk_bytes_and_timings(self) -> list[dict[str, Any]]:
        return [dict(chunk) for chunk in self.chunks]


def _is_lost(config: NetworkConfig, media_start: int, retry: int) -> bool:
    if config.loss_percent <= 0:
        return False
    block = media_start // config.packet_payload_bytes
    token = f"{config.loss_seed}:{block}:{retry}".encode()
    bucket = int.from_bytes(hashlib.sha256(token).digest()[:8], "big")
    return bucket / 2**64 < config.loss_percent / 100.0


def _packet_delivery(
    config: NetworkConfig, cursor: float, payload_bytes: int, media_start: int
) -> tuple[float, int, int, float]:
    """Return delivery time, retries, retransmitted bytes, and nominal send end."""
    wire_bytes = config.packet_header_bytes + payload_bytes
    send_end = cursor + wire_bytes / config.bytes_per_second
    one_way = config.rtt_ms / 2000.0
    retry = 0
    delivery = send_end + one_way
    retransmitted = 0
    while _is_lost(config, media_start, retry):
        if retry >= config.max_retries:
            break
        retry += 1
        retransmitted += wire_bytes
        retransmit_end = send_end + retry * (config.rtt_ms / 1000.0) + (
            wire_bytes / config.bytes_per_second
        )
        delivery = retransmit_end + one_way
    return delivery, retry, retransmitted, send_end


def simulate_transfer(
    data: bytes,
    chunk_size: int,
    config: NetworkConfig,
    *,
    chunked: bool = True,
) -> TransferResult:
    """Simulate an in-order reliable byte stream and application chunk delivery.

    Packet losses are retransmitted after one RTT. The model deliberately keeps
    chunk payloads in one response, so there is no artificial per-chunk RTT.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if len(data) == 0:
        raise ValueError("cannot transfer an empty stream")

    cursor = config.rtt_ms / 2000.0 + (
        config.response_header_bytes / config.bytes_per_second
    )
    packets: list[_Packet] = []
    chunks: list[dict[str, Any]] = []
    chunk_ranges = [
        (start, min(start + chunk_size, len(data)))
        for start in range(0, len(data), chunk_size)
    ]
    if not chunked:
        chunk_ranges = [(0, len(data))]

    initial_losses = 0
    retransmissions = 0
    retransmitted_bytes = 0
    wire_bytes = config.response_header_bytes

    for chunk_index, (start, end) in enumerate(chunk_ranges):
        if chunked:
            cursor += config.chunk_header_bytes / config.bytes_per_second
            wire_bytes += config.chunk_header_bytes
        first_payload_wire = cursor
        local = start
        while local < end:
            payload_end = min(local + config.packet_payload_bytes, end)
            payload_len = payload_end - local
            delivery, retries, resent, send_end = _packet_delivery(
                config, cursor, payload_len, local
            )
            packets.append(_Packet(local, payload_end, delivery))
            if retries and _is_lost(config, local, 0):
                initial_losses += 1
            retransmissions += retries
            retransmitted_bytes += resent
            wire_bytes += config.packet_header_bytes + payload_len + resent
            cursor = send_end
            local = payload_end
        chunks.append(
            {
                "index": chunk_index,
                "start_byte": start,
                "end_byte": end,
                "bytes": end - start,
                "payload_wire_start_s": first_payload_wire,
            }
        )

    prefix_ready: list[tuple[int, float]] = []
    running_max = 0.0
    for packet in packets:
        running_max = max(running_max, packet.delivery_s)
        prefix_ready.append((packet.media_end - 1, running_max))

    result = TransferResult(
        chunked=chunked,
        config=config,
        chunks=chunks,
        prefix_ready=prefix_ready,
        lost_initial_packets=initial_losses,
        retransmissions=retransmissions,
        packet_count=len(packets),
        retransmitted_payload_bytes=retransmitted_bytes,
        wire_bytes=wire_bytes,
    )

    previous_end: float | None = None
    previous_start: float | None = None
    for chunk in result.chunks:
        start_s = result.ready_at(chunk["start_byte"])
        end_s = result.ready_at(chunk["end_byte"] - 1)
        chunk["start_s"] = start_s
        chunk["end_s"] = end_s
        chunk["elapsed_ms"] = (end_s - start_s) * 1000.0
        chunk["gap_before_ms"] = (
            None if previous_end is None else (start_s - previous_end) * 1000.0
        )
        chunk["start_interval_ms"] = (
            None if previous_start is None else (start_s - previous_start) * 1000.0
        )
        previous_end = end_s
        previous_start = start_s
    return result


def summarise_timing(
    result: TransferResult, first_sample_end: int, total_bytes: int
) -> dict[str, Any]:
    chunks = result.chunks
    gaps = [chunk["gap_before_ms"] for chunk in chunks[1:]]
    sorted_gaps = sorted(gaps)
    file_end = result.ready_at(total_bytes - 1)
    first_sample_s = result.ready_at(first_sample_end - 1)

    def percentile(values: list[float], fraction: float) -> float:
        if not values:
            return 0.0
        index = math.ceil((len(values) - 1) * fraction)
        return sorted_values[index]

    sorted_values = sorted_gaps
    framing_bytes = sum(
        result.config.chunk_header_bytes for _ in chunks
    ) if result.chunked else 0
    media_bytes = total_bytes
    return {
        "chunked": result.chunked,
        "chunk_count": len(chunks),
        "media_bytes": media_bytes,
        "chunk_framing_bytes": framing_bytes,
        "chunk_framing_overhead_percent": (
            100.0 * framing_bytes / media_bytes if media_bytes else 0.0
        ),
        "first_sample_transport_ms": first_sample_s * 1000.0,
        "file_complete_ms": file_end * 1000.0,
        "packet_count": result.packet_count,
        "lost_initial_packets": result.lost_initial_packets,
        "retransmissions": result.retransmissions,
        "retransmitted_bytes": result.retransmitted_payload_bytes,
        "wire_bytes_including_headers": result.wire_bytes,
        "gap_p50_ms": percentile(gaps, 0.50),
        "gap_p95_ms": percentile(gaps, 0.95),
        "gap_max_ms": max(gaps, default=0.0),
        "gap_sum_ms": sum(gaps),
    }
