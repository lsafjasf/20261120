"""Minimal Dolby TrueHD major-sync-frame model with substream channel mapping.

A TrueHD stream carries up to 4 substreams inside a major sync frame.
Each substream has a 16-bit channel assignment; the decoder recombines
the substreams into the output channel order. The minimal configuration
is a single substream (substream 0) whose assignment alone defines the
output layout (identity recombination).

Frame layout modeled here (bytes):
  0..3   sync word 0xF8726F + format byte 0x28 (TrueHD)
  4      flags: bit7 = major-sync, bits4-6 = substream count
  5..6   frame length (bytes, big-endian)
  7..    per-substream 16-bit channel assignments, then data blocks
  last 4 CRC32 over all preceding bytes
"""
from __future__ import annotations

import struct
import zlib

SYNC = b"\xF8\x72\x6F\x28"          # 0xF8726F sync + TrueHD format byte
MAX_SUBSTREAMS = 4

# Canonical TrueHD channel order: bit i of the assignment word.
CHANNEL_ORDER = [
    "L", "C", "R", "Ls", "Rs", "LFE", "Lrs", "Rrs",
    "Cs", "Ts", "Lsd", "Rsd", "Lw", "Rw", "Lvh", "Rvh",
]

ASSIGNMENT_STEREO = (1 << CHANNEL_ORDER.index("L")) \
    | (1 << CHANNEL_ORDER.index("R"))               # minimal 2.0 config


def channels_of(assignment: int) -> list[str]:
    """Ordered output channel names for a 16-bit assignment word."""
    return [ch for i, ch in enumerate(CHANNEL_ORDER) if assignment >> i & 1]


def build_major_sync_frame(assignments: list[int],
                           blocks: list[bytes] | None = None) -> bytes:
    """Build one major sync frame with len(assignments) substreams."""
    if not 1 <= len(assignments) <= MAX_SUBSTREAMS:
        raise ValueError("TrueHD allows 1..4 substreams")
    blocks = blocks or [b""] * len(assignments)
    body = bytearray()
    for assignment in assignments:
        body += struct.pack(">H", assignment)
    for block in blocks:
        body += block
    header = bytearray(SYNC)
    header.append(0x80 | (len(assignments) << 4))   # major-sync flag + count
    header += struct.pack(">H", 4 + 1 + 2 + len(body) + 4)
    frame = bytes(header) + bytes(body)
    return frame + struct.pack(">I", zlib.crc32(frame) & 0xFFFFFFFF)


def parse_major_sync_frame(data: bytes) -> dict:
    if len(data) < 13 or data[:4] != SYNC:
        raise ValueError("not a TrueHD major sync frame")
    flags = data[4]
    if not flags & 0x80:
        raise ValueError("major-sync flag not set")
    n_sub = (flags >> 4) & 0x07
    length = struct.unpack(">H", data[5:7])[0]
    if length != len(data):
        raise ValueError(f"length mismatch: header={length} actual={len(data)}")
    crc_stored = struct.unpack(">I", data[-4:])[0]
    if crc_stored != (zlib.crc32(data[:-4]) & 0xFFFFFFFF):
        raise ValueError("CRC32 mismatch: frame corrupted in transit")
    assignments = []
    off = 7
    for _ in range(n_sub):
        assignments.append(struct.unpack(">H", data[off:off + 2])[0])
        off += 2
    return {
        "substreams": n_sub,
        "assignments": assignments,
        "channels": [channels_of(a) for a in assignments],
        "payload": data[off:-4],
    }


def map_channels(assignments: list[int]) -> list[tuple[int, int, str]]:
    """Recombine substreams into the output channel order.

    Returns (substream_index, substream_channel, output_channel) tuples.
    With a single substream this must be the identity mapping: substream
    channel i feeds output channel i in canonical order.
    """
    mapping: list[tuple[int, int, str]] = []
    for sub_idx, assignment in enumerate(assignments):
        for ch_idx, name in enumerate(channels_of(assignment)):
            mapping.append((sub_idx, ch_idx, name))
    return mapping
