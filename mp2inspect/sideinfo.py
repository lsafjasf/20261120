"""Layer II bit-allocation, SCFSI, scalefactors and CRC validation."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .bitstream import BitReader, BitstreamError, crc16_bits
from .header import Header
from .tables import (
    ALLOC_WIDTHS,
    SBLIMIT,
    allocation_table_index,
)


class SideInfoError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SideInfo:
    allocation_table: int
    sblimit: int
    bound: int
    allocation_bits: int
    scfsi_bits: int
    protected_bits: int
    scale_factor_bits: int
    side_info_start_bit: int
    protected_end_bit: int
    side_info_end_bit: int
    channels: int
    allocation: tuple[tuple[int, ...], ...]
    scfsi: tuple[tuple[int | None, ...], ...]
    scale_factors: tuple[tuple[tuple[int, int, int] | None, ...], ...]
    crc_stored: int | None
    crc_calculated: int | None
    crc_ok: bool | None

    def to_dict(self) -> dict:
        return asdict(self)


def _read_allocation(
    reader: BitReader,
    widths: tuple[int, ...],
    sblimit: int,
    bound: int,
    channels: int,
) -> tuple[tuple[int, ...], ...]:
    values = [[0] * sblimit for _ in range(channels)]
    for subband in range(bound):
        width = widths[subband]
        for channel in range(channels):
            values[channel][subband] = reader.read(width)
    for subband in range(bound, sblimit):
        value = reader.read(widths[subband])
        for channel in range(channels):
            values[channel][subband] = value
    return tuple(tuple(row) for row in values)


def parse_side_info(
    data: bytes,
    header: Header,
    frame_end: int,
    inferred_bitrate_kbps: float | None = None,
) -> SideInfo:
    if frame_end <= header.offset + 4 or frame_end > len(data):
        raise SideInfoError("frame is too short for Layer II side information")

    channels = header.channels
    if header.free_format:
        if inferred_bitrate_kbps is None:
            raise SideInfoError("free-format frame has no inferred bitrate")
        selected_bitrate = max(1, round(inferred_bitrate_kbps))
    else:
        assert header.bitrate_kbps is not None
        selected_bitrate = header.bitrate_kbps

    table = allocation_table_index(
        selected_bitrate, channels, header.sample_rate, lsf=False
    )
    widths = ALLOC_WIDTHS[table]
    sblimit = SBLIMIT[table]
    bound = min((header.mode_extension + 1) * 4, sblimit) if header.mode == 1 else sblimit

    crc_size = 2 if header.has_crc else 0
    side_start_byte = header.offset + 4 + crc_size
    side_start_bit = side_start_byte * 8
    frame_start_bit = header.offset * 8
    reader = BitReader(data, side_start_bit, frame_end * 8)

    try:
        allocation = _read_allocation(reader, widths, sblimit, bound, channels)
        allocation_bits = reader.pos - side_start_bit

        scfsi = [[None] * sblimit for _ in range(channels)]
        for subband in range(sblimit):
            for channel in range(channels):
                if allocation[channel][subband]:
                    scfsi[channel][subband] = reader.read(2)
        scfsi_bits = reader.pos - side_start_bit - allocation_bits
        protected_bits = reader.pos - side_start_bit
        protected_end_bit = reader.pos - frame_start_bit

        crc_stored = None
        crc_calculated = None
        crc_ok = None
        if header.has_crc:
            crc_stored = int.from_bytes(
                data[header.offset + 4 : header.offset + 6], "big"
            )
            protected_data = data[side_start_byte:frame_end]
            crc_calculated = crc16_bits(
                0xFFFF,
                (header.header_word & 0xFFFF).to_bytes(2, "big"),
                16,
            )
            crc_calculated = crc16_bits(
                crc_calculated, protected_data, protected_bits
            )
            crc_ok = crc_stored == crc_calculated

        scale_factors: list[list[tuple[int, int, int] | None]] = [
            [None] * sblimit for _ in range(channels)
        ]
        for subband in range(sblimit):
            for channel in range(channels):
                if not allocation[channel][subband]:
                    continue
                code = scfsi[channel][subband]
                assert code is not None
                if code == 0:
                    factors = tuple(reader.read(6) for _ in range(3))
                elif code == 2:
                    first = reader.read(6)
                    factors = (first, first, first)
                elif code == 1:
                    first, third = reader.read(6), reader.read(6)
                    factors = (first, first, third)
                else:
                    first, third = reader.read(6), reader.read(6)
                    factors = (first, third, third)
                scale_factors[channel][subband] = factors
        scale_factor_bits = (
            reader.pos
            - side_start_bit
            - allocation_bits
            - scfsi_bits
        )
    except BitstreamError as exc:
        raise SideInfoError(str(exc)) from exc

    return SideInfo(
        allocation_table=table,
        sblimit=sblimit,
        bound=bound,
        allocation_bits=allocation_bits,
        scfsi_bits=scfsi_bits,
        protected_bits=protected_bits,
        scale_factor_bits=scale_factor_bits,
        side_info_start_bit=side_start_bit - frame_start_bit,
        protected_end_bit=protected_end_bit,
        side_info_end_bit=reader.pos - frame_start_bit,
        channels=channels,
        allocation=allocation,
        scfsi=tuple(tuple(row) for row in scfsi),
        scale_factors=tuple(tuple(row) for row in scale_factors),
        crc_stored=crc_stored,
        crc_calculated=crc_calculated,
        crc_ok=crc_ok,
    )
