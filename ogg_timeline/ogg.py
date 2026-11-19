"""Minimal, strict Ogg page parser and packet demultiplexer."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


CAPTURE_PATTERN = b"OggS"
HEADER_SIZE = 27
CONTINUED_FLAG = 0x01
BOS_FLAG = 0x02
EOS_FLAG = 0x04
NO_GRANULE = -1


def _make_crc_table() -> tuple[int, ...]:
    table = []
    for value in range(256):
        remainder = value << 24
        for _ in range(8):
            if remainder & 0x80000000:
                remainder = ((remainder << 1) ^ 0x04C11DB7) & 0xFFFFFFFF
            else:
                remainder = (remainder << 1) & 0xFFFFFFFF
        table.append(remainder)
    return tuple(table)


CRC_TABLE = _make_crc_table()


def ogg_crc(data: bytes) -> int:
    """Return the non-reflected CRC-32 used by Ogg (polynomial 0x04c11db7)."""

    checksum = 0
    for byte in data:
        checksum = ((checksum << 8) & 0xFFFFFFFF) ^ CRC_TABLE[
            ((checksum >> 24) & 0xFF) ^ byte
        ]
    return checksum


@dataclass
class OggPage:
    index: int
    offset: int
    size: int
    version: int
    header_type: int
    granule_position: int
    serial: int
    sequence: int
    stored_crc: int
    computed_crc: int
    segments: tuple[int, ...]
    payload: bytes = field(repr=False)
    completed_packet_indices: list[int] = field(default_factory=list)
    ending_packet_indices: list[int] = field(default_factory=list)
    packet_fragment_count: int = 0

    @property
    def payload_size(self) -> int:
        return len(self.payload)

    @property
    def continued(self) -> bool:
        return bool(self.header_type & CONTINUED_FLAG)

    @property
    def bos(self) -> bool:
        return bool(self.header_type & BOS_FLAG)

    @property
    def eos(self) -> bool:
        return bool(self.header_type & EOS_FLAG)

    @property
    def crc_ok(self) -> bool:
        return self.stored_crc == self.computed_crc

    @property
    def has_granule(self) -> bool:
        return self.granule_position != NO_GRANULE

    @property
    def completed_packet_count(self) -> int:
        return sum(1 for value in self.segments if value < 255)


@dataclass
class Packet:
    index: int
    serial: int
    data: bytes = field(repr=False)
    start_page: int
    end_page: int
    continued_from_previous: bool
    eos: bool
    _header: bool = False
    _duration_samples: int | None = None
    _mode: int | None = None

    @property
    def length(self) -> int:
        return len(self.data)


@dataclass
class LogicalStream:
    serial: int
    pages: list[OggPage] = field(default_factory=list)
    packets: list[Packet] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class OggFile:
    data: bytes = field(repr=False)
    pages: list[OggPage]
    streams: dict[int, LogicalStream]
    warnings: list[str] = field(default_factory=list)


def parse_pages(data: bytes, *, resync: bool = True) -> OggFile:
    """Parse all physical Ogg pages and demultiplex their packets by serial.

    Pages with a bad CRC are retained and marked ``crc_ok == False`` so the
    caller can decide whether they are fatal.  Garbage between pages is
    skipped when ``resync`` is true and recorded in ``OggFile.warnings``.
    """

    pages: list[OggPage] = []
    warnings: list[str] = []
    offset = 0
    limit = len(data)

    while offset < limit:
        if data[offset:offset + 4] != CAPTURE_PATTERN:
            if not resync:
                raise ValueError(f"missing Ogg capture pattern at byte {offset}")
            next_offset = data.find(CAPTURE_PATTERN, offset + 1)
            if next_offset < 0:
                warnings.append(
                    f"discarded {limit - offset} trailing bytes without OggS"
                )
                break
            warnings.append(
                f"skipped {next_offset - offset} bytes before OggS at {next_offset}"
            )
            offset = next_offset

        if offset + HEADER_SIZE > limit:
            warnings.append(f"truncated Ogg header at byte {offset}")
            break

        header = data[offset:offset + HEADER_SIZE]
        version = header[4]
        header_type = header[5]
        granule = int.from_bytes(header[6:14], "little", signed=True)
        serial = int.from_bytes(header[14:18], "little")
        sequence = int.from_bytes(header[18:22], "little")
        stored_crc = int.from_bytes(header[22:26], "little")
        segment_count = header[26]
        table_end = offset + HEADER_SIZE + segment_count
        if table_end > limit:
            warnings.append(f"truncated segment table at byte {offset}")
            break

        segments = tuple(data[offset + HEADER_SIZE:table_end])
        page_end = table_end + sum(segments)
        if page_end > limit:
            warnings.append(
                f"truncated page payload at byte {offset}: "
                f"need {page_end - limit} more bytes"
            )
            break

        raw_page = bytearray(data[offset:page_end])
        raw_page[22:26] = b"\x00\x00\x00\x00"
        computed_crc = ogg_crc(bytes(raw_page))
        pages.append(
            OggPage(
                index=len(pages),
                offset=offset,
                size=page_end - offset,
                version=version,
                header_type=header_type,
                granule_position=granule,
                serial=serial,
                sequence=sequence,
                stored_crc=stored_crc,
                computed_crc=computed_crc,
                segments=segments,
                payload=data[table_end:page_end],
            )
        )
        offset = page_end

    streams = _demux_pages(pages)
    return OggFile(data=data, pages=pages, streams=streams, warnings=warnings)


def _demux_pages(pages: Iterable[OggPage]) -> dict[int, LogicalStream]:
    streams: dict[int, LogicalStream] = {}
    pending: dict[int, bytearray] = {}
    pending_start: dict[int, int] = {}

    for page in pages:
        stream = streams.setdefault(page.serial, LogicalStream(page.serial))
        stream.pages.append(page)

        if page.continued and page.serial not in pending:
            stream.warnings.append(
                f"page {page.index} is marked continued but has no prior "
                "packet fragment"
            )
        if not page.continued and page.serial in pending:
            stream.warnings.append(
                f"page {page.index} does not continue packet started on "
                f"page {pending_start[page.serial]}"
            )

        cursor = 0
        for lacing in page.segments:
            if page.serial not in pending:
                pending[page.serial] = bytearray()
                pending_start[page.serial] = page.index
            pending[page.serial].extend(page.payload[cursor:cursor + lacing])
            cursor += lacing
            if lacing == 255:
                page.packet_fragment_count += 1
                continue

            packet = Packet(
                index=len(stream.packets),
                serial=page.serial,
                data=bytes(pending[page.serial]),
                start_page=pending_start[page.serial],
                end_page=page.index,
                continued_from_previous=(
                    pending_start[page.serial] != page.index
                ),
                eos=page.eos,
            )
            stream.packets.append(packet)
            page.completed_packet_indices.append(packet.index)
            page.ending_packet_indices.append(packet.index)
            del pending[page.serial]
            del pending_start[page.serial]

        if page.continued and cursor != page.payload_size and page.segments:
            # Lacing always consumes the complete payload.  This check catches
            # a malformed page table that is internally inconsistent.
            stream.warnings.append(
                f"page {page.index} payload/lacing size mismatch"
            )

    for serial, fragment in pending.items():
        stream = streams[serial]
        stream.warnings.append(
            f"final packet fragment from page {pending_start[serial]} "
            f"({len(fragment)} bytes) is incomplete"
        )
    return streams


def parse_file(path: str | Path) -> OggFile:
    return parse_pages(Path(path).read_bytes())
