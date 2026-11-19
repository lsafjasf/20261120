"""SRT packet model (simplified but faithful to UDT/SRT layout).

Header layout (SRT data packet, 16 bytes):
  [0]   F(1) | SeqNo(31)          F=0 data, F=1 control
  [1]   PP(2) | O(1) | KK(2) | R(1) | MsgNo(26)
  [2]   Timestamp(32)
  [3]   DestSocketID(32)
Control packets carry a 16-byte SRT control header (type/subtype + info)
followed by a type-specific body.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field


class CtrlType(enum.IntEnum):
    HANDSHAKE = 0x0000
    KEEPALIVE = 0x0001
    ACK = 0x0002
    NAK = 0x0004
    SHUTDOWN = 0x0005
    ACKACK = 0x0006
    KMREQ = 0x8003
    KMRSP = 0x8004


class HSStage(enum.IntEnum):
    INDUCTION = 1
    CONCLUSION = 2


SRT_DATA_HDR = 16          # bytes
SRT_CTRL_HDR = 16          # bytes
AES_BLOCK = 16             # AES-CTR keeps length, SRT pads payload to block
KM_BODY = 40               # simplified KM message body (KEK-wrapped SEK etc.)


@dataclass
class Packet:
    """One wire packet. `size` is the on-wire length in bytes."""
    ptype: str                       # 'ctrl' or 'data'
    size: int
    ctrl: CtrlType | None = None
    seq: int = -1                    # data packet sequence number
    ack_seq: int = -1                # ACK: last contiguous seq received
    nak_list: tuple = ()             # NAK: lost seq numbers
    stage: HSStage | None = None     # handshake stage
    cookie: int = 0                  # handshake cookie
    km_ok: bool = True               # KMRSP status
    payload: bytes = b""
    sent_at: float = 0.0             # filled by the link for RTT sampling
    info: dict = field(default_factory=dict)

    @staticmethod
    def data(seq: int, payload: bytes, ts: int = 0) -> "Packet":
        # SRT encrypts with AES-CTR; ciphertext length == plaintext length,
        # but the SRT payload is padded to a multiple of the AES block.
        padded = (len(payload) + AES_BLOCK - 1) // AES_BLOCK * AES_BLOCK
        return Packet("data", SRT_DATA_HDR + padded, seq=seq, payload=payload)

    @staticmethod
    def ctrl_packet(ctype: CtrlType, **kw) -> "Packet":
        body = 0
        if ctype in (CtrlType.KMREQ, CtrlType.KMRSP):
            body = KM_BODY
        elif ctype == CtrlType.HANDSHAKE:
            body = 48  # HSv5 handshake body (version, flags, cookie, IP...)
        elif ctype in (CtrlType.ACK, CtrlType.ACKACK):
            body = 16
        elif ctype == CtrlType.NAK:
            body = 4 * max(1, len(kw.get("nak_list", (1,))))
        return Packet("ctrl", SRT_CTRL_HDR + body, ctrl=ctype, **kw)
