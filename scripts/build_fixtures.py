#!/usr/bin/env python3
"""Build deterministic Layer II fixtures that exercise framing edge cases."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mp2inspect.bitstream import crc16_bits
from mp2inspect.tables import ALLOC_WIDTHS, SBLIMIT, allocation_table_index

BITRATES = (0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384, 0)


class BitWriter:
    def __init__(self) -> None:
        self.bits: list[int] = []

    def put(self, value: int, width: int) -> None:
        if value < 0 or value >= 1 << width:
            raise ValueError(f"{value} does not fit in {width} bits")
        self.bits.extend((value >> shift) & 1 for shift in range(width - 1, -1, -1))

    def to_bytes(self) -> bytes:
        padded = self.bits + [0] * (-len(self.bits) % 8)
        output = bytearray()
        for start in range(0, len(padded), 8):
            value = 0
            for bit in padded[start : start + 8]:
                value = (value << 1) | bit
            output.append(value)
        return bytes(output)


def make_header(
    bitrate_index: int,
    sample_rate_index: int,
    padding: int,
    mode: int,
    mode_extension: int,
    has_crc: bool,
) -> int:
    word = 0xFFE00000
    word |= 0x3 << 19
    word |= 0x2 << 17
    word |= (0 if has_crc else 1) << 16
    word |= bitrate_index << 12
    word |= sample_rate_index << 10
    word |= padding << 9
    word |= mode << 6
    word |= mode_extension << 4
    return word


def make_frame(
    *,
    bitrate_index: int = 8,
    sample_rate_index: int = 0,
    padding: int = 0,
    mode: int = 0,
    mode_extension: int = 0,
    has_crc: bool = True,
    allocation_overrides: dict[tuple[int, int], int] | None = None,
) -> bytes:
    bitrate = BITRATES[bitrate_index]
    sample_rate = (44100, 48000, 32000)[sample_rate_index]
    channels = 1 if mode == 3 else 2
    table = allocation_table_index(bitrate, channels, sample_rate)
    widths = ALLOC_WIDTHS[table]
    sblimit = SBLIMIT[table]
    frame_length = 144000 * bitrate // sample_rate + padding
    bound = min((mode_extension + 1) * 4, sblimit) if mode == 1 else sblimit

    overrides = allocation_overrides or {}
    allocation = [
        [overrides.get((channel, subband), 0) for subband in range(sblimit)]
        for channel in range(channels)
    ]
    for subband in range(bound, sblimit):
        shared = allocation[0][subband]
        for channel in range(channels):
            allocation[channel][subband] = shared

    writer = BitWriter()
    for subband in range(bound):
        width = widths[subband]
        for channel in range(channels):
            value = allocation[channel][subband]
            writer.put(value, width)
    for subband in range(bound, sblimit):
        writer.put(allocation[0][subband], widths[subband])
    for subband in range(sblimit):
        for channel in range(channels):
            if allocation[channel][subband]:
                writer.put(0, 2)
                writer.put(0, 18)

    protected_bits = sum(
        widths[subband]
        for subband in range(bound)
        for _ in range(channels)
    )
    protected_bits += sum(widths[subband] for subband in range(bound, sblimit))
    protected_bits += 2 * sum(
        1 for row in allocation for value in row if value
    )
    if writer.bits and protected_bits > len(writer.bits):
        raise AssertionError("protected-bit accounting exceeded side info")

    header_word = make_header(
        bitrate_index,
        sample_rate_index,
        padding,
        mode,
        mode_extension,
        has_crc,
    )
    header = header_word.to_bytes(4, "big")
    side = writer.to_bytes()
    if has_crc:
        crc = crc16_bits(
            0xFFFF, (header_word & 0xFFFF).to_bytes(2, "big"), 16
        )
        crc = crc16_bits(crc, side, protected_bits)
        prefix = header + crc.to_bytes(2, "big") + side
    else:
        prefix = header + side
    if len(prefix) > frame_length:
        raise AssertionError("side information exceeds frame length")
    return prefix + bytes(frame_length - len(prefix))


def main() -> None:
    samples = ROOT / "samples"
    samples.mkdir(exist_ok=True)

    alternating = []
    for padding in (0, 1, 0, 1, 1, 0, 1, 0):
        alternating.append(
            make_frame(
                bitrate_index=8,
                sample_rate_index=0,
                padding=padding,
                mode=0,
                has_crc=True,
            )
        )
    (samples / "padding_alternating.mp2").write_bytes(b"".join(alternating))

    rate_switch = []
    for sample_rate_index in (2, 0, 1, 2, 0, 1):
        for padding in (0, 1):
            rate_switch.append(
                make_frame(
                    bitrate_index=8,
                    sample_rate_index=sample_rate_index,
                    padding=padding,
                    mode=0,
                    has_crc=False,
                )
            )
    (samples / "rate_padding_switch.mp2").write_bytes(b"".join(rate_switch))

    signatures = (
        {(0, 0): 1, (1, 1): 2},
        {(0, 2): 3, (1, 4): 1},
        {(0, 5): 4, (1, 6): 5},
        {(0, 7): 6, (1, 8): 7},
    )
    channel_frames = [
        make_frame(
            bitrate_index=8,
            sample_rate_index=0,
            padding=index & 1,
            mode=2,
            has_crc=True,
            allocation_overrides=signature,
        )
        for index, signature in enumerate(signatures)
    ]
    (samples / "channel_order_packets.mp2").write_bytes(b"".join(channel_frames))

    packet_lengths = []
    seed = 0x5EED
    remaining = sum(map(len, channel_frames))
    while remaining:
        seed = (1103515245 * seed + 12345) & 0x7FFFFFFF
        packet_lengths.append(min(remaining, 1 + seed % 61))
        remaining -= packet_lengths[-1]

    manifest = (
        "{\n"
        '  "channel_order_packets": {\n'
        f"    \"bytes\": {sum(map(len, channel_frames))},\n"
        f"    \"packet_lengths\": {packet_lengths}\n"
        "  }\n"
        "}\n"
    )
    (samples / "fixture_manifest.json").write_text(manifest, encoding="utf-8")


if __name__ == "__main__":
    main()
