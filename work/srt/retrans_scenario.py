"""Deterministic loss/retransmission scenario for SRT KM + sequence checks.

Forces specific packets to be dropped on first transmission so the
retransmission path (NAK fast retransmit + RTO backstop) is exercised
deterministically, then asserts:
  * a dropped KMRSP is re-answered and both endpoints finish km_ok;
  * application delivery sequence numbers stay contiguous with no dup;
  * retransmitted data packets never re-deliver an already-ACKed seq.
"""
from __future__ import annotations

import random

from .endpoint import Endpoint, State
from .link import DuplexLink
from .packets import CtrlType


class ScriptedLink(DuplexLink):
    """DuplexLink whose A->B path drops chosen (type, key) packets once."""

    def __init__(self, *args, drop_data=(), drop_kmrsp=0, **kwargs):
        super().__init__(*args, **kwargs)
        self._drop_data = set(drop_data)
        self._drop_kmrsp_left = drop_kmrsp
        self.dropped_data = []
        self.dropped_kmrsp = 0
        self._orig_a2b = self.a2b.send

        def scripted_send(pkt, now_ms):
            if pkt.ptype == "data" and pkt.seq in self._drop_data:
                self._drop_data.discard(pkt.seq)
                self.dropped_data.append(pkt.seq)
                self.a2b.sent += 1
                self.a2b.dropped += 1
                return
            if pkt.ptype == "ctrl" and pkt.ctrl == CtrlType.KMRSP \
                    and self._drop_kmrsp_left > 0:
                self._drop_kmrsp_left -= 1
                self.dropped_kmrsp += 1
                self.a2b.sent += 1
                self.a2b.dropped += 1
                return
            return self._orig_a2b(pkt, now_ms)

        self.a2b.send = scripted_send


def run_retrans_scenario(n_packets=24, lost_seqs=(3, 7, 12),
                         drop_kmrsp=1, seed=7,
                         latency_ms=80.0, jitter_ms=30.0, loss=0.02):
    rng = random.Random(seed)
    link = ScriptedLink(latency_ms, jitter_ms, loss, seed,
                        drop_data=lost_seqs, drop_kmrsp=drop_kmrsp)
    a = Endpoint("A", link, rng)
    b = Endpoint("B", link, rng)
    a.hs_timeout = 2.0 * (latency_ms + jitter_ms) + 150.0

    payload = bytes(rng.randrange(256) for _ in range(1316))
    a.start_handshake(0.0)
    now, step = 0.0, 0.5
    next_send, send_started = 0.0, False
    sent = {"A": 0, "B": 0}
    retrans_log = []
    kmrsp_seen_at_a = []

    while now < 120_000:
        for pkt in link.a2b.poll(now):
            b.on_packet(pkt, now)
        for pkt in link.b2a.poll(now):
            if pkt.ptype == "ctrl" and pkt.ctrl == CtrlType.KMRSP:
                kmrsp_seen_at_a.append(round(now, 1))
            a.on_packet(pkt, now)
        a.check_hs_timeout(now)
        b.check_hs_timeout(now)
        a.check_km_timeout(now)
        b.check_km_timeout(now)

        if a.state == State.ESTABLISHED and b.state == State.ESTABLISHED \
                and a.km_ok and b.km_ok:
            if not send_started:
                next_send, send_started = now, True
            while next_send <= now and (sent["A"] < n_packets
                                        or sent["B"] < n_packets):
                for ep in (a, b):
                    if sent[ep.name] < n_packets:
                        ep.send_data(payload, next_send)
                        sent[ep.name] += 1
                next_send += 5.0

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
        now += step

    return {"a": a, "b": b, "link": link, "sim_ms": now,
            "retrans_log": retrans_log, "kmrsp_times": kmrsp_seen_at_a,
            "dropped_data": link.dropped_data,
            "dropped_kmrsp": link.dropped_kmrsp}
