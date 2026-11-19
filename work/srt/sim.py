"""Discrete-event driver running a bidirectional SRT session A<->B."""
from __future__ import annotations

import random

from .endpoint import Endpoint, State
from .link import DuplexLink

STEP_MS = 0.5
SEND_INTERVAL_MS = 5.0
RECONNECT_BACKOFF_MS = 100.0
MAX_RECONNECTS = 5
MAX_TIME_MS = 120_000.0


def run_session(latency_ms: float, jitter_ms: float, loss: float,
                n_packets: int, seed: int = 42, blackout_ms: float = 0.0,
                payload_size: int = 1316,
                payload: bytes | None = None) -> dict:
    link = DuplexLink(latency_ms, jitter_ms, loss, seed)
    if blackout_ms:
        link.a2b.blackout_until = blackout_ms
        link.b2a.blackout_until = blackout_ms
    rng = random.Random(seed + 1)
    a = Endpoint("A", link, rng)
    b = Endpoint("B", link, rng)
    # Handshake timeout must cover a full RTT plus loss recovery margin.
    hs_timeout = 2.0 * (latency_ms + jitter_ms) + 150.0
    a.hs_timeout = hs_timeout
    if payload is None:
        payload = bytes(rng.randrange(256) for _ in range(payload_size))

    a.start_handshake(0.0)
    now, next_send = 0.0, 0.0
    sent = {"A": 0, "B": 0}
    reconnect_at: float | None = None
    retrans_log: list[tuple[str, list[int]]] = []
    send_started = False

    while now < MAX_TIME_MS:
        for pkt in link.a2b.poll(now):
            b.on_packet(pkt, now)
        for pkt in link.b2a.poll(now):
            a.on_packet(pkt, now)
        a.check_hs_timeout(now)
        b.check_hs_timeout(now)
        a.check_km_timeout(now)
        b.check_km_timeout(now)

        if a.state == State.FAILED and reconnect_at is None \
                and a.reconnects < MAX_RECONNECTS:
            reconnect_at = now + RECONNECT_BACKOFF_MS
        if reconnect_at is not None and now >= reconnect_at:
            a.reconnect(now)
            reconnect_at = None

        if a.state == State.ESTABLISHED and b.state == State.ESTABLISHED \
                and a.km_ok and b.km_ok:
            if not send_started:
                next_send = now
                send_started = True
            while next_send <= now and (sent["A"] < n_packets
                                        or sent["B"] < n_packets):
                for ep in (a, b):
                    if sent[ep.name] < n_packets:
                        ep.send_data(payload, next_send)
                        sent[ep.name] += 1
                next_send += SEND_INTERVAL_MS

        for ep in (a, b):
            ep.periodic(now)
            resent = ep.retransmit_due(now)
            if resent:
                retrans_log.append((ep.name, resent))

        if sent["A"] == n_packets and sent["B"] == n_packets \
                and len(a.delivered) >= n_packets \
                and len(b.delivered) >= n_packets \
                and not a.unacked and not b.unacked:
            break
        now += STEP_MS

    return {
        "endpoints": (a, b),
        "link": link,
        "sim_ms": now,
        "retrans_log": retrans_log,
        "completed": len(a.delivered) >= n_packets
        and len(b.delivered) >= n_packets,
    }
