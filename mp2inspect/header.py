"""MPEG-1 Audio Layer II frame-header decoding."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from .tables import BITRATES_KBPS, MODE_NAMES, SAMPLE_RATES

SYNC_MASK = 0xFFE00000
SYNC_VALUE = 0xFFE00000
HEADER_SIZE = 4

EMPHASIS_NAMES = ("none", "50/15_ms", "reserved", "ccitt_j17")


@dataclass(frozen=True, slots=True)
class Header:
    offset: int
    header_word: int
    version: int
    layer: int
    protection_bit: int
    has_crc: bool
    bitrate_index: int
    bitrate_kbps: int | None
    sample_rate_index: int
    sample_rate: int
    padding: int
    private_bit: int
    mode: int
    mode_name: str
    mode_extension: int
    copyright_bit: int
    original_bit: int
    emphasis: int
    emphasis_name: str
    channels: int
    free_format: bool
    frame_length: int | None

    def to_dict(self) -> dict:
        return asdict(self)


def infer_free_bitrate(
    frame_length: int | None, sample_rate: int, padding: int
) -> float | None:
    """Infer a free-format bitrate from a frame size.

    The frame length gives a half-open interval. Prefer the integer kbit/s
    value in that interval because free-format encoders conventionally use an
    integer nominal bitrate.
    """
    if frame_length is None:
        return None
    lower = (frame_length - padding) * sample_rate / 144000.0
    upper = (frame_length - padding + 1) * sample_rate / 144000.0
    integer_candidate = math.ceil(lower)
    if integer_candidate < upper:
        return float(integer_candidate)
    return (lower + upper) / 2.0


def parse_header(data: bytes, offset: int = 0) -> Header | None:
    """Decode one header, returning ``None`` when it is not MPEG-1 Layer II."""
    if offset < 0 or offset + HEADER_SIZE > len(data):
        return None

    word = int.from_bytes(data[offset : offset + HEADER_SIZE], "big")
    if word & SYNC_MASK != SYNC_VALUE:
        return None

    version = (word >> 19) & 0x3
    layer = (word >> 17) & 0x3
    if version != 0x3 or layer != 0x2:
        return None

    protection_bit = (word >> 16) & 0x1
    bitrate_index = (word >> 12) & 0xF
    sample_rate_index = (word >> 10) & 0x3
    padding = (word >> 9) & 0x1
    mode = (word >> 6) & 0x3
    emphasis = word & 0x3

    if bitrate_index == 0xF or sample_rate_index == 0x3 or emphasis == 0x2:
        return None

    bitrate_kbps = BITRATES_KBPS[bitrate_index]
    sample_rate = SAMPLE_RATES[sample_rate_index]
    channels = 1 if mode == 3 else 2
    free_format = bitrate_index == 0

    if free_format:
        frame_length = None
    else:
        assert bitrate_kbps is not None
        frame_length = (144000 * bitrate_kbps) // sample_rate + padding
        if frame_length < HEADER_SIZE:
            return None

    return Header(
        offset=offset,
        header_word=word,
        version=version,
        layer=layer,
        protection_bit=protection_bit,
        has_crc=protection_bit == 0,
        bitrate_index=bitrate_index,
        bitrate_kbps=bitrate_kbps,
        sample_rate_index=sample_rate_index,
        sample_rate=sample_rate,
        padding=padding,
        private_bit=(word >> 8) & 0x1,
        mode=mode,
        mode_name=MODE_NAMES[mode],
        mode_extension=(word >> 4) & 0x3,
        copyright_bit=(word >> 3) & 0x1,
        original_bit=(word >> 2) & 0x1,
        emphasis=emphasis,
        emphasis_name=EMPHASIS_NAMES[emphasis],
        channels=channels,
        free_format=free_format,
        frame_length=frame_length,
    )
