"""Small LSB-first bit reader used by the Vorbis setup parser."""

from __future__ import annotations


class BitstreamError(ValueError):
    """The packet ended before a complete field could be read."""


def ilog(value: int) -> int:
    """Return the Vorbis ``ov_ilog()`` value (number of significant bits)."""

    if value < 0:
        raise ValueError("ilog is undefined for negative values")
    return value.bit_length()


class BitReader:
    """Read integers least-significant bit first, as Ogg Vorbis does."""

    __slots__ = ("data", "bitpos")

    def __init__(self, data: bytes, bitpos: int = 0) -> None:
        self.data = data
        self.bitpos = bitpos

    @property
    def bits_left(self) -> int:
        return len(self.data) * 8 - self.bitpos

    def read(self, count: int) -> int:
        if count < 0:
            raise ValueError("bit count must be non-negative")
        if count == 0:
            return 0
        end = self.bitpos + count
        if end > len(self.data) * 8:
            raise BitstreamError(
                f"truncated bitstream: need {count} bits at bit {self.bitpos}"
            )

        first_byte = self.bitpos // 8
        last_byte = (end + 7) // 8
        chunk = int.from_bytes(self.data[first_byte:last_byte], "little")
        value = (chunk >> (self.bitpos % 8)) & ((1 << count) - 1)
        self.bitpos = end
        return value

    def skip(self, count: int) -> None:
        if count < 0:
            raise ValueError("bit count must be non-negative")
        if self.bitpos + count > len(self.data) * 8:
            raise BitstreamError(
                f"truncated bitstream: cannot skip {count} bits at bit "
                f"{self.bitpos}"
            )
        self.bitpos += count


def lookup1_values(entries: int, dimensions: int) -> int:
    """Largest integer ``r`` for which ``r ** dimensions <= entries``."""

    if entries < 1 or dimensions < 1:
        return 0
    low, high = 1, max(1, entries)
    while low <= high:
        middle = (low + high) // 2
        value = 1
        for _ in range(dimensions):
            value *= middle
            if value > entries:
                break
        if value <= entries:
            low = middle + 1
        else:
            high = middle - 1
    return max(1, high)
