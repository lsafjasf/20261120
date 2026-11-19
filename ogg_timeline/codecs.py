"""Codec-specific Ogg packet inspection for Vorbis and Opus."""

from __future__ import annotations

from dataclasses import dataclass, field

from .bits import BitReader, BitstreamError, ilog, lookup1_values
from .ogg import LogicalStream, Packet


class CodecError(ValueError):
    """A codec header or packet is malformed."""


@dataclass
class CodecInfo:
    codec: str
    channels: int
    sample_rate: int
    header_packet_count: int
    headers_valid: bool = True
    warnings: list[str] = field(default_factory=list)


@dataclass
class OpusInfo(CodecInfo):
    pre_skip: int = 0
    input_sample_rate: int = 0
    output_gain_q8: int = 0
    mapping_family: int = 0


@dataclass
class VorbisInfo(CodecInfo):
    blocksize_small: int = 0
    blocksize_large: int = 0
    modes: tuple[bool, ...] = ()
    mode_bits: int = 0


@dataclass(frozen=True)
class OpusPacketInfo:
    config: int
    frame_count: int
    frame_samples: int
    duration_samples: int
    stereo: bool
    packing_code: int


OPUS_FRAME_US = {
    0: 10_000,
    1: 20_000,
    2: 40_000,
    3: 60_000,
}


def parse_opus_toc(packet: bytes) -> OpusPacketInfo:
    """Parse the duration of an RFC 6716 Opus packet from its TOC byte."""

    if not packet:
        raise CodecError("empty Opus packet")
    toc = packet[0]
    config = toc >> 3
    packing = toc & 0x03
    if config < 12:
        frame_us = OPUS_FRAME_US[config & 3]
    elif config < 16:
        frame_us = 10_000 if (config & 1) == 0 else 20_000
    else:
        frame_us = (2_500, 5_000, 10_000, 20_000)[config & 3]

    if packing == 0:
        frame_count = 1
    elif packing in (1, 2):
        frame_count = 2
    else:
        if len(packet) < 2:
            raise CodecError("truncated Opus code-3 framing byte")
        frame_count = packet[1] & 0x3F
        if frame_count == 0:
            raise CodecError("Opus code-3 packet contains zero frames")

    frame_samples = frame_us * 48 // 1000
    duration = frame_count * frame_samples
    if duration > 5_760:
        raise CodecError("Opus packet duration exceeds 120 ms")
    return OpusPacketInfo(
        config=config,
        frame_count=frame_count,
        frame_samples=frame_samples,
        duration_samples=duration,
        stereo=bool(toc & 0x04),
        packing_code=packing,
    )


def parse_vorbis_identification(packet: bytes) -> VorbisInfo:
    if len(packet) < 30 or packet[:7] != b"\x01vorbis":
        raise CodecError("not a Vorbis identification header")
    if packet[7:11] != b"\x00\x00\x00\x00":
        raise CodecError("unsupported Vorbis version")
    channels = packet[11]
    sample_rate = int.from_bytes(packet[12:16], "little")
    blocksize_bits = packet[28]
    blocksize_small = 1 << (blocksize_bits & 0x0F)
    blocksize_large = 1 << (blocksize_bits >> 4)
    if channels == 0 or sample_rate == 0:
        raise CodecError("invalid Vorbis channel count or sample rate")
    if not 64 <= blocksize_small <= blocksize_large <= 8192:
        raise CodecError("invalid Vorbis block sizes")
    if not packet[29] & 1:
        raise CodecError("Vorbis identification framing bit is clear")
    return VorbisInfo(
        codec="vorbis",
        channels=channels,
        sample_rate=sample_rate,
        header_packet_count=3,
        blocksize_small=blocksize_small,
        blocksize_large=blocksize_large,
    )


def _read_vorbis_codebook(
    reader: BitReader,
) -> tuple[int, int, int]:
    if reader.read(24) != 0x564342:
        raise CodecError("invalid Vorbis codebook sync pattern")
    dimensions = reader.read(16)
    entries = reader.read(24)
    if dimensions == 0 or entries == 0:
        raise CodecError("empty Vorbis codebook")
    if ilog(dimensions) + ilog(entries) > 24:
        raise CodecError("Vorbis codebook dimensions/entries are too large")
    if entries > 1_000_000:
        raise CodecError("Vorbis codebook has too many entries")

    ordered = reader.read(1)
    if ordered:
        current_length = reader.read(5) + 1
        current_entry = 0
        while current_entry < entries:
            number = reader.read(ilog(entries - current_entry))
            if number > entries - current_entry:
                raise CodecError("Vorbis ordered codebook overruns entries")
            current_entry += number
            current_length += 1
    else:
        sparse = reader.read(1)
        for _ in range(entries):
            if sparse:
                present = reader.read(1)
                if present:
                    reader.read(5)
            else:
                reader.read(5)

    map_type = reader.read(4)
    if map_type == 0:
        return dimensions, entries, map_type
    if map_type not in (1, 2):
        raise CodecError("unsupported Vorbis codebook mapping type")

    reader.skip(32)  # minimum value
    reader.skip(32)  # delta value
    value_bits = reader.read(4) + 1
    reader.read(1)  # sequence flag
    if map_type == 1:
        quantized_values = lookup1_values(entries, dimensions)
    else:
        quantized_values = entries * dimensions
    if quantized_values > 10_000_000:
        raise CodecError("Vorbis codebook value table is unreasonably large")
    reader.skip(quantized_values * value_bits)
    return dimensions, entries, map_type


def _read_vorbis_floors(
    reader: BitReader, codebook_count: int
) -> tuple[int, list[int]]:
    floor_count = reader.read(6) + 1
    floor_types: list[int] = []
    for _ in range(floor_count):
        floor_type = reader.read(16)
        floor_types.append(floor_type)
        if floor_type == 0:
            reader.read(8)   # order
            reader.read(16)  # rate
            reader.read(16)  # bark map size
            reader.read(6)   # amplitude bits
            reader.read(8)   # amplitude offset
            book_count = reader.read(4) + 1
            for _ in range(book_count):
                book = reader.read(8)
                if book >= codebook_count:
                    raise CodecError("Vorbis floor0 references an invalid book")
        elif floor_type == 1:
            partitions = reader.read(5)
            partition_classes = [reader.read(4) for _ in range(partitions)]
            max_class = max(partition_classes, default=-1)
            class_dimensions: list[int] = []
            class_subclasses: list[int] = []
            for _ in range(max_class + 1):
                class_dimensions.append(reader.read(3) + 1)
                subclasses = reader.read(2)
                class_subclasses.append(subclasses)
                if subclasses:
                    class_book = reader.read(8)
                    if class_book >= codebook_count:
                        raise CodecError(
                            "Vorbis floor1 references an invalid class book"
                        )
                for _ in range(1 << subclasses):
                    reader.read(8)
            reader.read(2)  # multiplier
            range_bits = reader.read(4)
            post_count = sum(
                class_dimensions[value] for value in partition_classes
            )
            for _ in range(post_count):
                reader.read(range_bits)
        else:
            raise CodecError(f"unsupported Vorbis floor type {floor_type}")
    return floor_count, floor_types


def _read_vorbis_residues(reader: BitReader) -> int:
    residue_count = reader.read(6) + 1
    for _ in range(residue_count):
        residue_type = reader.read(16)
        if residue_type not in (0, 1, 2):
            raise CodecError(f"unsupported Vorbis residue type {residue_type}")
        reader.read(24)  # begin
        reader.read(24)  # end
        reader.read(24)  # grouping
        partitions = reader.read(6) + 1
        reader.read(8)  # phrasebook
        books = 0
        for _ in range(partitions):
            cascade = reader.read(3)
            if reader.read(1):
                cascade |= reader.read(5) << 3
            books += cascade.bit_count()
        for _ in range(books):
            reader.read(8)
    return residue_count


def _read_vorbis_mappings(
    reader: BitReader, channels: int, floor_count: int, residue_count: int
) -> int:
    mapping_count = reader.read(6) + 1
    for _ in range(mapping_count):
        mapping_type = reader.read(16)
        if mapping_type != 0:
            raise CodecError(f"unsupported Vorbis mapping type {mapping_type}")
        submaps = reader.read(4) + 1 if reader.read(1) else 1
        if reader.read(1):
            coupling_steps = reader.read(8) + 1
            channel_bits = ilog(channels - 1)
            for _ in range(coupling_steps):
                reader.read(channel_bits)
                reader.read(channel_bits)
        if reader.read(2) != 0:
            raise CodecError("reserved Vorbis mapping bits are non-zero")
        if submaps > 1:
            for _ in range(channels):
                reader.read(4)
        for _ in range(submaps):
            reader.read(8)  # unused time submap
            floor = reader.read(8)
            residue = reader.read(8)
            if floor >= floor_count:
                raise CodecError("Vorbis mapping references an invalid floor")
            if residue >= residue_count:
                raise CodecError("Vorbis mapping references an invalid residue")
    return mapping_count


def parse_vorbis_setup(info: VorbisInfo, packet: bytes) -> None:
    if len(packet) < 8 or packet[:7] != b"\x05vorbis":
        raise CodecError("not a Vorbis setup header")
    reader = BitReader(packet, 7 * 8)
    codebook_count = reader.read(8) + 1
    for _ in range(codebook_count):
        _read_vorbis_codebook(reader)

    time_count = reader.read(6) + 1
    for _ in range(time_count):
        if reader.read(16) >= 1:
            raise CodecError("unsupported Vorbis time transform")

    floor_count, _ = _read_vorbis_floors(reader, codebook_count)
    residue_count = _read_vorbis_residues(reader)
    _read_vorbis_mappings(
        reader, info.channels, floor_count, residue_count
    )

    mode_count = reader.read(6) + 1
    modes: list[bool] = []
    for _ in range(mode_count):
        modes.append(bool(reader.read(1)))
        reader.read(16)  # window type
        reader.read(16)  # transform type
        reader.read(8)   # mapping index
    if reader.read(1) != 1:
        raise CodecError("Vorbis setup framing bit is clear")
    info.modes = tuple(modes)
    info.mode_bits = ilog(mode_count - 1)


def vorbis_packet_blockflag(info: VorbisInfo, packet: bytes) -> bool:
    """Read an audio packet's mode and return its long/short block flag."""

    if not info.modes:
        raise CodecError("Vorbis setup header was not parsed")
    reader = BitReader(packet)
    if reader.read(1) != 0:
        raise CodecError("expected a Vorbis audio packet")
    mode = reader.read(info.mode_bits)
    if mode >= len(info.modes):
        raise CodecError(f"Vorbis packet references invalid mode {mode}")
    return info.modes[mode]


def _parse_opus_header(packet: bytes) -> OpusInfo:
    if len(packet) < 19 or packet[:8] != b"OpusHead":
        raise CodecError("not an OpusHead identification header")
    if packet[8] != 1:
        raise CodecError("unsupported OpusHead version")
    channels = packet[9]
    pre_skip = int.from_bytes(packet[10:12], "little")
    input_rate = int.from_bytes(packet[12:16], "little")
    gain = int.from_bytes(packet[16:18], "little", signed=True)
    mapping_family = packet[18]
    if channels == 0:
        raise CodecError("OpusHead channel count is zero")
    return OpusInfo(
        codec="opus",
        channels=channels,
        sample_rate=48_000,
        header_packet_count=2,
        pre_skip=pre_skip,
        input_sample_rate=input_rate,
        output_gain_q8=gain,
        mapping_family=mapping_family,
    )


def analyze_codec(stream: LogicalStream) -> CodecInfo | None:
    """Identify a logical stream and annotate every audio packet duration."""

    if not stream.packets:
        return None
    first = stream.packets[0].data
    if first.startswith(b"\x01vorbis"):
        return _analyze_vorbis(stream)
    if first.startswith(b"OpusHead"):
        return _analyze_opus(stream)
    return None


def _analyze_vorbis(stream: LogicalStream) -> VorbisInfo:
    info = parse_vorbis_identification(stream.packets[0].data)
    stream.packets[0]._header = True
    if len(stream.packets) < 2 or not stream.packets[1].data.startswith(
        b"\x03vorbis"
    ):
        info.headers_valid = False
        info.warnings.append("missing Vorbis comment header")
    else:
        stream.packets[1]._header = True
    if len(stream.packets) < 3:
        info.headers_valid = False
        info.warnings.append("missing Vorbis setup header")
        return info

    setup = stream.packets[2]
    if not setup.data.startswith(b"\x05vorbis"):
        info.headers_valid = False
        info.warnings.append("third Vorbis packet is not the setup header")
        return info
    setup._header = True
    try:
        parse_vorbis_setup(info, setup.data)
    except (CodecError, BitstreamError, ValueError) as error:
        info.headers_valid = False
        info.warnings.append(f"Vorbis setup parse failed: {error}")

    previous_flag = False
    for packet in stream.packets[info.header_packet_count:]:
        packet._header = False
        try:
            blockflag = vorbis_packet_blockflag(info, packet.data)
            previous_size = (
                info.blocksize_large if previous_flag else info.blocksize_small
            )
            current_size = (
                info.blocksize_large if blockflag else info.blocksize_small
            )
            packet._duration_samples = (previous_size + current_size) // 4
            packet._mode = int(blockflag)
            previous_flag = blockflag
        except (CodecError, BitstreamError, ValueError) as error:
            info.warnings.append(
                f"Vorbis audio packet {packet.index} parse failed: {error}"
            )
    return info


def _analyze_opus(stream: LogicalStream) -> OpusInfo:
    info = _parse_opus_header(stream.packets[0].data)
    stream.packets[0]._header = True
    if len(stream.packets) < 2 or not stream.packets[1].data.startswith(
        b"OpusTags"
    ):
        info.headers_valid = False
        info.warnings.append("missing Opus comment header")
    else:
        stream.packets[1]._header = True

    for packet in stream.packets[info.header_packet_count:]:
        packet._header = False
        try:
            toc = parse_opus_toc(packet.data)
            packet._duration_samples = toc.duration_samples
            packet._mode = toc.config
        except CodecError as error:
            info.warnings.append(
                f"Opus audio packet {packet.index} parse failed: {error}"
            )
    return info
