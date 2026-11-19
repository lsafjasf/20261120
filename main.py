"""SRT simulator report: HS handshake, KM response, retransmission metrics."""
from __future__ import annotations

from srt_sim.sim import run_session
from srt_sim.truehd import (ASSIGNMENT_STEREO, build_major_sync_frame,
                            map_channels, parse_major_sync_frame)


def fmt_rtt(ep) -> str:
    s = ep.rtt_stats()
    return (f"avg={s['avg_ms']:.1f}ms jitter={s['jitter_ms']:.2f}ms "
            f"({s['samples']} samples)")


def retrans_seqs(result, name):
    seqs = [s for ep, batch in result["retrans_log"] if ep == name
            for s in batch]
    return seqs


def report_session(title, result, n_packets):
    a, b = result["endpoints"]
    link = result["link"]
    print(f"\n=== {title} ===")
    print(f"  session completed : {result['completed']} "
          f"(simulated {result['sim_ms']:.0f} ms)")
    print(f"  handshake failures: A={a.handshake_failures} "
          f"B={b.handshake_failures}   reconnects: A={a.reconnects} "
          f"B={b.reconnects}")
    print(f"  RTT  A: {fmt_rtt(a)}")
    print(f"  RTT  B: {fmt_rtt(b)}")
    print(f"  link loss rate    : A->B {link.a2b.loss_rate:.2%}  "
          f"B->A {link.b2a.loss_rate:.2%}")
    for ep in (a, b):
        lens = sorted(set(ep.encrypted_payload_lens))
        print(f"  encrypted payload {ep.name}: {len(ep.encrypted_payload_lens)}"
              f" pkts, wire sizes={lens} bytes, "
              f"total={ep.encrypted_bytes} B")
    for ep, name in ((a, "A"), (b, "B")):
        seqs = retrans_seqs(result, name)
        repeat = len(seqs) - len(set(seqs))
        print(f"  retrans window {name} ({ep.retrans_by_nak} NAK + "
              f"{ep.retrans_by_rto} RTO): seqs={seqs}")
        print(f"    resend-of-ACKed attempts={ep.acked_resend_attempts}  "
              f"repeat resends of still-unacked={repeat}")
    for ep in (a, b):
        ok_order = ep.delivered == sorted(ep.delivered)
        print(f"  delivery {ep.name}: {len(ep.delivered)}/{n_packets} pkts, "
              f"in-order={ok_order}, dup deliveries={ep.dup_deliveries} "
              f"(dup arrivals dropped={ep.dup_arrivals})")


def scenario_baseline():
    r = run_session(latency_ms=60, jitter_ms=40, loss=0.05, n_packets=60,
                    seed=7)
    report_session("Scenario 1: bidirectional session, 60ms+40ms jitter, "
                   "5% loss", r, 60)


def scenario_high_latency():
    r = run_session(latency_ms=200, jitter_ms=100, loss=0.10, n_packets=60,
                    seed=11)
    report_session("Scenario 2: high latency 200ms+100ms jitter, 10% loss "
                   "(retransmission policy check)", r, 60)
    a, b = r["endpoints"]
    assert a.acked_resend_attempts == 0 and b.acked_resend_attempts == 0
    assert a.dup_deliveries == 0 and b.dup_deliveries == 0
    print("  POLICY OK: no already-ACKed packet was ever resubmitted; "
          "no duplicate delivery.")


def scenario_handshake_failure():
    # 800 ms outage at t=0 kills every handshake attempt (3 tries x
    # hs_timeout), forcing one declared failure and one reconnect.
    r = run_session(latency_ms=60, jitter_ms=40, loss=0.05, n_packets=30,
                    seed=3, blackout_ms=800)
    report_session("Scenario 3: 800ms outage -> handshake failure + "
                   "reconnect", r, 30)
    a, _ = r["endpoints"]
    assert a.handshake_failures >= 1 and a.reconnects >= 1
    print("  RECOVERY OK: session re-established after reconnect.")


def scenario_truehd():
    # One TrueHD major sync frame per SRT packet, single substream (2.0).
    audio_block = bytes(range(256)) * 4            # 1024 B synthetic audio
    frame = build_major_sync_frame([ASSIGNMENT_STEREO], [audio_block])
    r = run_session(latency_ms=80, jitter_ms=30, loss=0.03, n_packets=40,
                    seed=5, payload=frame)
    report_session("Scenario 4: TrueHD single-substream stream over SRT",
                   r, 40)
    a, b = r["endpoints"]
    checked = 0
    for ep in (a, b):
        for raw in ep.app_inbox:
            info = parse_major_sync_frame(raw)     # raises if corrupted
            assert info["substreams"] == 1
            assert info["channels"] == [["L", "R"]]
            mapping = map_channels(info["assignments"])
            assert mapping == [(0, 0, "L"), (0, 1, "R")]
            assert info["payload"] == audio_block
            checked += 1
    print(f"  TrueHD frames verified: {checked} (single substream, "
          f"assignment=0x{ASSIGNMENT_STEREO:04X})")
    print("  channel mapping (minimal config): sub0.ch0->L, sub0.ch1->R "
          "[identity, OK]")


if __name__ == "__main__":
    scenario_baseline()
    scenario_high_latency()
    scenario_handshake_failure()
    scenario_truehd()
    print("\nAll scenarios passed.")
