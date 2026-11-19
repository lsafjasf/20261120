"""MSB-first bit access and the MPEG-1 CRC-16 primitive."""

from __future__ import annotations


class BitstreamError(ValueError):
    pass


class BitReader:
    def __init__(
        self,
        data: bytes,
        bit_offset: int = 0,
        bit_limit: int | None = None,
    ) -> None:
        self.data = data
        self.pos = bit_offset
        self.limit = len(data) * 8 if bit_limit is None else bit_limit

    def read(self, count: int) -> int:
        if count < 0 or self.pos + count > self.limit:
            raise BitstreamError(
                f"need {count} bits at bit {self.pos}, limit is {self.limit}"
            )
        value = 0
        for _ in range(count):
            byte = self.data[self.pos >> 3]
            value = (value << 1) | ((byte >> (7 - (self.pos & 7))) & 1)
            self.pos += 1
        return value

    def skip(self, count: int) -> None:
        if count < 0 or self.pos + count > self.limit:
            raise BitstreamError(
                f"cannot skip {count} bits at bit {self.pos}, limit is {self.limit}"
            )
        self.pos += count


def crc16_bits(initial: int, data: bytes, bit_count: int, bit_offset: int = 0) -> int:
    """CRC-16/ANSI as used by MPEG audio, with no final XOR."""
    crc = initial & 0xFFFF
    for bit_index in range(bit_offset, bit_offset + bit_count):
        bit = (data[bit_index >> 3] >> (7 - (bit_index & 7))) & 1
        crc ^= bit << 15
        if crc & 0x8000:
            crc = ((crc << 1) ^ 0x8005) & 0xFFFF
        else:
            crc = (crc << 1) & 0xFFFF
    return crc


def mpeg_frame_crc(header_word: int, protected_data: bytes, bit_count: int) -> int:
    """Calculate the Layer II CRC over header bits 16..31 and protected bits."""
    header_tail = (header_word & 0xFFFF).to_bytes(2, "big")
    crc = crc16_bits(0xFFFF, header_tail, 16)
    return crc16_bits(crc, protected_data, bit_count)
