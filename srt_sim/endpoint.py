"""Simulated SRT endpoint: HSv5 handshake, KM exchange, ARQ retransmission."""
from __future__ import annotations

import enum
import random
import statistics

from .link import DuplexLink
from .packets import CtrlType, HSStage, Packet

HS_TIMEOUT_MS = 250.0        # handshake retransmit / fail timeout
HS_MAX_TRIES = 3             # attempts before the handshake is declared failed
RTO_MIN_MS = 10.0            # minimum retransmission timeout
RTO_DEFAULT_MS = 250.0       # used before the first RTT sample
RTT_ALPHA = 0.125            # RFC 6298 SRTT smoothing
RTT_BETA = 0.25              # RFC 6298 RTTVAR smoothing
REORDER_TOLERANCE = 10       # pkts that must pass a gap before it is NAKed


class State(enum.Enum):
    INIT = "init"
    HS_INDUCTION = "hs-induction"
    HS_CONCLUSION = "hs-conclusion"
    KM = "km"
    ESTABLISHED = "established"
    FAILED = "failed"


class Endpoint:
    def __init__(self, name: str, link: DuplexLink, rng: random.Random):
        self.name = name
        self.link = link
        self.rng = rng
        self.state = State.INIT
        self.cookie = 0
        self.peer_cookie = 0
        self.hs_tries = 0
        self.hs_deadline = 0.0
        self._hs_sent_at = 0.0
        self.hs_timeout = HS_TIMEOUT_MS
        self._ind_rsp_sent_at: float | None = None
        self._km_sent_at: float | None = None
        self._last_ackack_ts = -1.0
        self.km_ok = False
        # metrics
        self.handshake_failures = 0
        self.reconnects = 0
        self.rtt_samples: list[float] = []
        self.srtt: float | None = None
        self.rttvar: float | None = None
        # data plane
        self.next_seq = 0
        # seq -> [pkt, first_sent, last_sent, retries]
        self.unacked: dict[int, list] = {}
        self.retrans_window: list[int] = []     # seqs currently pending retransmission
        self.retransmitted = 0
        self.retrans_by_nak = 0
        self.retrans_by_rto = 0
        self.acked_seqs: set[int] = set()       # every seq covered by an ACK
        self.acked_resend_attempts = 0          # invariant: must stay 0
        self.recv_next = 0
        self.delivered: list[int] = []          # seqs delivered to the app, in order
        self.dup_arrivals = 0                   # duplicate wire copies, dropped
        self._delivered_set: set[int] = set()
        self._loss_set: set[int] = set()        # NAK-reported losses
        self._last_nak_at = 0.0
        self.out_of_order: dict[int, Packet] = {}
        self.encrypted_bytes = 0
        self.encrypted_payload_lens: list[int] = []
        self.app_inbox: list[bytes] = []

    # ------------------------------------------------------------------ util
    def _send(self, pkt: Packet, now: float) -> None:
        self.link.direction(self.name).send(pkt, now)

    def _record_rtt(self, rtt: float) -> None:
        self.rtt_samples.append(rtt)
        if self.srtt is None:
            self.srtt, self.rttvar = rtt, rtt / 2
        else:
            self.rttvar = (1 - RTT_BETA) * self.rttvar + RTT_BETA * abs(self.srtt - rtt)
            self.srtt = (1 - RTT_ALPHA) * self.srtt + RTT_ALPHA * rtt

    @property
    def rto(self) -> float:
        """Backstop timeout. NAKs are the primary retransmit trigger, so
        this stays conservative (>= 4 RTT) to avoid spurious resends
        while a NAK repair is still in flight on high-latency links."""
        if self.srtt is None:
            return RTO_DEFAULT_MS
        return max(4.0 * self.srtt, self.srtt + 4 * (self.rttvar or 0.0))

    @property
    def nak_retrans_thresh(self) -> float:
        """Fast-retransmit gate for NAKed packets (~1.5 RTT)."""
        if self.srtt is None:
            return 100.0
        return max(RTO_MIN_MS, 1.5 * self.srtt)

    # ------------------------------------------------------------- handshake
    def start_handshake(self, now: float) -> None:
        """Caller side: send induction, or conclusion if we have a cookie."""
        self.hs_tries += 1
        if self.hs_tries > HS_MAX_TRIES:
            self.state = State.FAILED
            self.handshake_failures += 1
            return
        if self.peer_cookie:
            self.state = State.HS_CONCLUSION
            pkt = Packet.ctrl_packet(CtrlType.HANDSHAKE, stage=HSStage.CONCLUSION,
                                     cookie=self.peer_cookie)
        else:
            self.state = State.HS_INDUCTION
            pkt = Packet.ctrl_packet(CtrlType.HANDSHAKE, stage=HSStage.INDUCTION,
                                     cookie=0)
        self._send(pkt, now)
        self.hs_deadline = now + self.hs_timeout
        self._hs_sent_at = now

    def reconnect(self, now: float) -> None:
        """Restart the state machine after a failed handshake."""
        self.reconnects += 1
        self.state = State.INIT
        self.hs_tries = 0
        self.peer_cookie = 0
        self.start_handshake(now)

    # ------------------------------------------------------------ KM exchange
    def _send_kmreq(self, now: float) -> None:
        self.state = State.KM
        self._km_sent_at = now
        self._send(Packet.ctrl_packet(CtrlType.KMREQ), now)

    def check_km_timeout(self, now: float) -> None:
        """KMREQ is retransmitted until a KMRSP arrives (SRT does this
        periodically); KM delivery is not left to the data-phase RTO."""
        if self.state in (State.KM, State.ESTABLISHED) and not self.km_ok \
                and self._km_sent_at is not None \
                and now - self._km_sent_at >= self.hs_timeout:
            self._send_kmreq(now)

    # -------------------------------------------------------------- dispatch
    def on_packet(self, pkt: Packet, now: float) -> None:
        if pkt.ctrl == CtrlType.HANDSHAKE:
            self._on_handshake(pkt, now)
        elif pkt.ctrl == CtrlType.KMREQ:
            # Listener answers with KMRSP (keying material accepted).
            self._send(Packet.ctrl_packet(CtrlType.KMRSP, km_ok=True), now)
        elif pkt.ctrl == CtrlType.KMRSP:
            if pkt.km_ok:
                self.km_ok = True
                self.state = State.ESTABLISHED
            else:
                self.state = State.FAILED
                self.handshake_failures += 1
        elif pkt.ctrl == CtrlType.ACK:
            self._on_ack(pkt, now)
        elif pkt.ctrl == CtrlType.ACKACK:
            # RTT sample at the ACK sender: ACK -> ACKACK round trip.
            ack_ts = pkt.info.get("ack_ts", -1.0)
            if ack_ts > self._last_ackack_ts:
                self._last_ackack_ts = ack_ts
                self._record_rtt(now - ack_ts)
        elif pkt.ctrl == CtrlType.NAK:
            self._on_nak(pkt, now)
        elif pkt.ptype == "data":
            self._on_data(pkt, now)

    def _on_handshake(self, pkt: Packet, now: float) -> None:
        if pkt.stage == HSStage.INDUCTION:
            if pkt.cookie == 0:
                # Listener: fresh induction request, derive cookie and answer.
                self.cookie = (hash((self.name, "srt")) & 0x7FFFFFFF) or 1
                self._send(Packet.ctrl_packet(CtrlType.HANDSHAKE,
                                              stage=HSStage.INDUCTION,
                                              cookie=self.cookie), now)
                self._ind_rsp_sent_at = now
        elif pkt.stage == HSStage.CONCLUSION:
            if pkt.cookie not in (self.cookie, self.peer_cookie):
                return  # cookie mismatch: drop, peer will time out
            if self._ind_rsp_sent_at is not None:
                # Listener: induction->conclusion round trip seeds the RTT.
                self._record_rtt(now - self._ind_rsp_sent_at)
                self._ind_rsp_sent_at = None
            if self.state == State.HS_CONCLUSION:
                # Caller: conclusion response confirms the handshake.
                self._record_rtt(now - self._hs_sent_at)
                self.state = State.ESTABLISHED
            elif self.state != State.ESTABLISHED:
                # Listener: conclusion arrived, confirm and start KM.
                self.state = State.ESTABLISHED
                self._send(Packet.ctrl_packet(CtrlType.HANDSHAKE,
                                              stage=HSStage.CONCLUSION,
                                              cookie=self.cookie), now)
                self._send_kmreq(now)
        # Caller received induction response carrying the listener cookie.
        if self.state == State.HS_INDUCTION and pkt.cookie:
            self._record_rtt(now - self._hs_sent_at)
            self.peer_cookie = pkt.cookie
            self.state = State.HS_CONCLUSION
            self._send(Packet.ctrl_packet(CtrlType.HANDSHAKE,
                                          stage=HSStage.CONCLUSION,
                                          cookie=self.peer_cookie), now)
            self._hs_sent_at = now
            self._send_kmreq(now)

    def check_hs_timeout(self, now: float) -> None:
        if self.state in (State.HS_INDUCTION, State.HS_CONCLUSION) \
                and now >= self.hs_deadline:
            self.start_handshake(now)  # retry; may flip state to FAILED

    # ------------------------------------------------------------ data plane
    def send_data(self, payload: bytes, now: float) -> Packet | None:
        if self.state != State.ESTABLISHED or not self.km_ok:
            return None
        pkt = Packet.data(self.next_seq, payload)
        self.next_seq += 1
        self.unacked[pkt.seq] = [pkt, now, now, 0]
        self.encrypted_bytes += pkt.size
        self.encrypted_payload_lens.append(pkt.size)
        self._send(pkt, now)
        return pkt

    def _on_data(self, pkt: Packet, now: float) -> None:
        seq = pkt.seq
        # Always ACK the current contiguous point, even for duplicates:
        # a silent receiver deadlocks the sender's retransmission timer.
        self._send(Packet.ctrl_packet(CtrlType.ACK, ack_seq=self.recv_next - 1),
                   now)
        if seq in self._delivered_set:
            self.dup_arrivals += 1     # duplicate copy: dropped, not delivered
            return
        if seq < self.recv_next:
            self.dup_arrivals += 1
            return
        if seq == self.recv_next:
            self._deliver(pkt)
            self.recv_next += 1
            while self.recv_next in self.out_of_order:
                self._deliver(self.out_of_order.pop(self.recv_next))
                self.recv_next += 1
            self._loss_set = {s for s in self._loss_set if s >= self.recv_next}
        else:
            self.out_of_order[seq] = pkt
            # Per-packet reorder tolerance: a missing seq is only NAKed
            # once REORDER_TOLERANCE later packets have arrived, so
            # in-flight (merely reordered) packets are never falsely
            # reported lost, even while an earlier gap stalls recv_next.
            for missing in range(self.recv_next, seq):
                if seq - missing >= REORDER_TOLERANCE \
                        and missing not in self.out_of_order:
                    self._loss_set.add(missing)
        # NAK the gaps.
        self._maybe_send_nak(now)

    def _maybe_send_nak(self, now: float) -> None:
        if self._loss_set:
            self._send(Packet.ctrl_packet(CtrlType.NAK,
                                          nak_list=tuple(sorted(self._loss_set))),
                       now)
            self._last_nak_at = now

    def periodic(self, now: float) -> None:
        """Receiver housekeeping: re-report unrepaired losses roughly
        every half RTT so a lost NAK or lost retransmission is recovered
        without waiting for the sender's backstop RTO."""
        if not self._loss_set:
            return
        interval = max(20.0, (self.srtt or 100.0) / 2.0)
        if now - self._last_nak_at >= interval:
            self._maybe_send_nak(now)

    def _deliver(self, pkt: Packet) -> None:
        self._delivered_set.add(pkt.seq)
        self.delivered.append(pkt.seq)
        self.app_inbox.append(pkt.payload)

    @property
    def dup_deliveries(self) -> int:
        """Duplicates actually delivered to the app. Must always be 0."""
        return len(self.delivered) - len(self._delivered_set)

    def _on_ack(self, pkt: Packet, now: float) -> None:
        if pkt.ack_seq < 0:
            return
        # Answer with ACKACK so the ACK sender can measure the RTT.
        self._send(Packet.ctrl_packet(CtrlType.ACKACK,
                                      info={"ack_ts": pkt.sent_at}), now)
        for seq in [s for s in self.unacked if s <= pkt.ack_seq]:
            del self.unacked[seq]
        self.acked_seqs.update(s for s in range(pkt.ack_seq + 1))
        # ACKed packets must leave the retransmission window immediately.
        self.retrans_window = [s for s in self.retrans_window
                               if s in self.unacked]

    def _on_nak(self, pkt: Packet, now: float) -> None:
        for seq in pkt.nak_list:
            if seq in self.unacked and seq not in self.retrans_window:
                self.retrans_window.append(seq)

    def retransmit_due(self, now: float) -> list[int]:
        """Retransmit NAKed packets plus anything older than RTO.

        Returns the sequence numbers actually resent. A packet is never
        resent if it has already been ACKed (it would no longer be in
        `unacked`), and NAK-driven entries are rate-limited by RTO so a
        high-latency link cannot cause double submission.
        """
        sent: list[int] = []
        for seq in list(self.retrans_window):
            entry = self.unacked.get(seq)
            if entry is None:            # already ACKed: drop from window
                self.retrans_window.remove(seq)
                continue
            if seq in self.acked_seqs:   # defensive: never resubmit an ACKed pkt
                self.acked_resend_attempts += 1
                self.retrans_window.remove(seq)
                continue
            pkt, first_sent, last_sent, retries = entry
            if now - last_sent < self.nak_retrans_thresh:
                continue                 # too early: avoid duplicate submit
            self.unacked[seq] = [pkt, first_sent, now, retries + 1]
            self._send(pkt, now)
            self.retrans_window.remove(seq)
            self.retransmitted += 1
            self.retrans_by_nak += 1
            sent.append(seq)
        # Backstop RTO: like TCP, only the oldest unacked packet carries
        # the timer, with exponential backoff, so a slow NAK repair can
        # never trigger a retransmission storm across the whole window.
        if self.unacked:
            seq = min(self.unacked)
            pkt, first_sent, last_sent, retries = self.unacked[seq]
            backoff = 2 ** min(retries, 5)
            if seq not in self.retrans_window \
                    and now - last_sent >= self.rto * backoff:
                if seq in self.acked_seqs:
                    self.acked_resend_attempts += 1
                else:
                    self.unacked[seq] = [pkt, first_sent, now, retries + 1]
                    self._send(pkt, now)
                    self.retransmitted += 1
                    self.retrans_by_rto += 1
                    sent.append(seq)
        return sent

    # --------------------------------------------------------------- metrics
    def rtt_stats(self) -> dict:
        if not self.rtt_samples:
            return {"avg_ms": 0.0, "jitter_ms": 0.0, "samples": 0}
        avg = statistics.fmean(self.rtt_samples)
        # RFC 3550 style jitter: mean deviation of consecutive RTT samples.
        jit = 0.0
        for prev, cur in zip(self.rtt_samples, self.rtt_samples[1:]):
            jit += (abs(cur - prev) - jit) / 16.0
        return {"avg_ms": avg, "jitter_ms": jit, "samples": len(self.rtt_samples)}
