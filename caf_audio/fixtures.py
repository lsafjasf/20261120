"""Deterministic CAF fixtures used by tests and the acceptance report."""

from __future__ import annotations

import struct

SAMPLE_RATE = 48000.0
SAMPLE_FRAMES = (
    (1000, -1000),
    (32767, -32768),
    (-1, 1),
    (256, -256),
)


def chunk(chunk_type: bytes, payload: bytes) -> bytes:
    if len(chunk_type) != 4:
        raise ValueError("chunk type must be four bytes")
    return struct.pack(">4sq", chunk_type, len(payload)) + payload


def description_payload(
    *,
    flags: int = 0,
    channels: int = 2,
    bits: int = 16,
    bytes_per_packet: int = 4,
) -> bytes:
    return struct.pack(
        ">d4sIIIII",
        SAMPLE_RATE,
        b"lpcm",
        flags,
        bytes_per_packet,
        1,
        channels,
        bits,
    )


def stereo_layout_payload() -> bytes:
    return struct.pack(">III", (101 << 16) | 2, 0, 0)


def packet_table_payload(frame_count: int) -> bytes:
    return struct.pack(">qqii", frame_count, frame_count, 0, 0)


def pcm16_samples(little_endian: bool) -> bytes:
    prefix = "<" if little_endian else ">"
    return b"".join(
        struct.pack(prefix + "hh", left, right)
        for left, right in SAMPLE_FRAMES
    )


def build_caf(
    *,
    description: bytes,
    chunks: list[bytes],
) -> bytes:
    return b"caff" + struct.pack(">HH", 1, 0) + description + b"".join(chunks)


def build_misordered_big_endian() -> bytes:
    audio = pcm16_samples(little_endian=False)
    layout = chunk(b"chan", stereo_layout_payload())
    free = chunk(b"free", b"\x00\x00\x00\x00")
    data = chunk(b"data", struct.pack(">I", 0) + audio)
    packet_table = chunk(b"pakt", packet_table_payload(len(SAMPLE_FRAMES)))
    description = chunk(b"desc", description_payload(flags=0))
    return build_caf(
        description=b"",
        chunks=[layout, free, data, packet_table, description],
    )


def build_little_endian() -> bytes:
    audio = pcm16_samples(little_endian=True)
    chunks = [
        chunk(b"desc", description_payload(flags=0x2)),
        chunk(b"chan", stereo_layout_payload()),
        chunk(b"pakt", packet_table_payload(len(SAMPLE_FRAMES))),
        chunk(b"data", struct.pack(">I", 0) + audio),
    ]
    return build_caf(description=b"", chunks=chunks)


def build_invalid_endian_flag() -> bytes:
    audio = pcm16_samples(little_endian=False)
    chunks = [
        chunk(b"desc", description_payload(flags=0x4)),
        chunk(b"data", struct.pack(">I", 0) + audio),
    ]
    return build_caf(description=b"", chunks=chunks)
