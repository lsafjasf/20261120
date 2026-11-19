# SRT Handshake / KM / Retransmission Simulator

Discrete-event simulation of an SRT-style secure reliable transport
session between two built-in mock endpoints (A = caller, B = listener).

## Run

```sh
python3 main.py
```

## What is modeled

- **HS handshake (HSv5)**: induction -> cookie -> conclusion, with
  timeout/retry (3 tries), failure declaration and reconnect backoff.
- **KM exchange**: KMREQ/KMRSP keying-material response with periodic
  KMREQ retransmission until KMRSP arrives.
- **Encrypted data**: AES-CTR model, payload padded to 16-byte blocks,
  16-byte SRT data header (wire size reported per packet).
- **Retransmission**: NAK-driven fast retransmit (per-packet reorder
  tolerance of 10, ~1.5xRTT resubmit gate) + TCP-style backstop RTO on
  the oldest unacked packet with exponential backoff. ACKed packets are
  evicted from the retransmission window immediately and never
  resubmitted (asserted via `acked_resend_attempts == 0`).
- **RTT**: measured SRT-style via ACK/ACKACK round trips; RFC 6298
  SRTT/RTTVAR smoothing; RFC 3550-style jitter of RTT samples.
- **TrueHD**: major sync frame build/parse with CRC32; single-substream
  (minimal 2.0) channel mapping verified as identity (sub0.ch0->L,
  sub0.ch1->R) after transport over the lossy link.

## Scenarios (main.py)

1. Bidirectional 60-packet session, 60 ms + 40 ms jitter, 5% loss.
2. High-latency 200 ms + 100 ms jitter, 10% loss: retransmission policy
   verification (no resubmit of ACKed packets, no duplicate delivery).
3. 800 ms outage: handshake failure count + reconnect count, recovery.
4. TrueHD single-substream stream transported over SRT, mapping check.

## Layout

- `srt_sim/packets.py`  packet model and wire sizes
- `srt_sim/link.py`     lossy/jittered duplex link
- `srt_sim/endpoint.py` endpoint state machine (HS/KM/ARQ/RTT)
- `srt_sim/sim.py`      discrete-event driver
- `srt_sim/truehd.py`   TrueHD frame + channel mapping model
- `main.py`             scenario runner and report
