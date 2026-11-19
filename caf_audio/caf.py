"""Core Audio Format container parsing and linear-PCM sample access."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import io
import math
import os
import struct
from typing import BinaryIO, Iterator

from .errors import (
    CafFormatError,
    InvalidEndianFlagError,
    MissingChunkError,
    UnsupportedFormatError,
)

CAF_FILE_TYPE = b"caff"
CAF_VERSION = 1

FORMAT_LINEAR_PCM = b"lpcm"
FLAG_IS_FLOAT = 0x1
FLAG_IS_LITTLE_ENDIAN = 0x2
KNOWN_LPCM_FLAGS = FLAG_IS_FLOAT | FLAG_IS_LITTLE_ENDIAN

LAYOUT_USE_DESCRIPTIONS = 0x00000000
LAYOUT_USE_BITMAP = 0x00010000

CHANNEL_LABELS = {
    1: "Left",
    2: "Right",
    3: "Center",
    4: "LFE Screen",
    5: "Left Surround",
    6: "Right Surround",
    7: "Left Center",
    8: "Right Center",
    9: "Center Surround",
    10: "Left Surround Direct",
    11: "Right Surround Direct",
    12: "Top Center Surround",
    13: "Vertical Height Left",
    14: "Vertical Height Center",
    15: "Vertical Height Right",
    16: "Top Back Left",
    17: "Top Back Center",
    18: "Top Back Right",
}

BITMAP_CHANNELS = [
    "Left",
    "Right",
    "Center",
    "LFE Screen",
    "Left Surround",
    "Right Surround",
    "Left Center",
    "Right Center",
    "Center Surround",
    "Left Surround Direct",
    "Right Surround Direct",
    "Top Center Surround",
    "Vertical Height Left",
    "Vertical Height Center",
    "Vertical Height Right",
    "Top Back Left",
    "Top Back Center",
    "Top Back Right",
]

LAYOUT_TAGS = {
    (100 << 16) | 1: ("Mono", ["Left"]),
    (101 << 16) | 2: ("Stereo", ["Left", "Right"]),
    (102 << 16) | 2: ("Stereo Headphones", ["Left", "Right"]),
    (103 << 16) | 2: ("Matrix Stereo", ["Left", "Right"]),
    (104 << 16) | 2: ("Mid/Side", ["Mid", "Side"]),
    (105 << 16) | 2: ("XY", ["X", "Y"]),
    (106 << 16) | 2: ("Binaural", ["Left", "Right"]),
    (107 << 16) | 2: ("Left/Right", ["Left", "Right"]),
    (108 << 16) | 2: ("Headphones", ["Left", "Right"]),
    (121 << 16) | 4: ("Quadraphonic", ["Left", "Right", "Left Surround", "Right Surround"]),
    (124 << 16) | 4: ("Pentagonal", ["Left", "Right", "Center", "Left Surround", "Right Surround"]),
    (125 << 16) | 5: ("Hexagonal", ["Left", "Right", "Center", "Left Surround", "Right Surround", "Center Surround"]),
    (126 << 16) | 6: ("5.1", ["Left", "Right", "Center", "LFE Screen", "Left Surround", "Right Surround"]),
}


@dataclass(frozen=True)
class Chunk:
    chunk_type: str
    size: int
    header_offset: int
    data_offset: int
    end_offset: int
    payload: bytes = field(default=b"", repr=False)


@dataclass(frozen=True)
class AudioDescription:
    sample_rate: float
    format_id: str
    format_flags: int
    bytes_per_packet: int
    frames_per_packet: int
    channels_per_frame: int
    bits_per_channel: int
    sample_rate_bytes: bytes = field(default=b"", repr=False)

    @property
    def is_linear_pcm(self) -> bool:
        return self.format_id == FORMAT_LINEAR_PCM.decode("latin-1")

    @property
    def is_float(self) -> bool:
        return bool(self.format_flags & FLAG_IS_FLOAT)

    @property
    def is_little_endian(self) -> bool:
        return self.is_linear_pcm and bool(
            self.format_flags & FLAG_IS_LITTLE_ENDIAN
        )

    @property
    def is_big_endian(self) -> bool:
        return self.is_linear_pcm and not self.is_little_endian

    @property
    def byte_order(self) -> str:
        if not self.is_linear_pcm:
            return "not-applicable"
        return "little" if self.is_little_endian else "big"

    @property
    def bytes_per_sample(self) -> int:
        if self.channels_per_frame == 0 or self.bytes_per_packet == 0:
            return 0
        if self.bytes_per_packet % self.channels_per_frame:
            raise CafFormatError(
                "bytesPerPacket is not divisible by channelsPerFrame"
            )
        return self.bytes_per_packet // self.channels_per_frame

    @property
    def sample_kind(self) -> str:
        if not self.is_linear_pcm:
            return "compressed"
        if self.is_float:
            return f"float{self.bits_per_channel}"
        return f"int{self.bits_per_channel}"

    def as_dict(self) -> dict:
        return {
            "sample_rate": self.sample_rate,
            "sample_rate_hex": self.sample_rate_bytes.hex(),
            "format_id": self.format_id,
            "format_flags": f"0x{self.format_flags:08x}",
            "sample_kind": self.sample_kind,
            "bytes_per_packet": self.bytes_per_packet,
            "frames_per_packet": self.frames_per_packet,
            "channels_per_frame": self.channels_per_frame,
            "bits_per_channel": self.bits_per_channel,
            "bytes_per_sample": self.bytes_per_sample,
            "little_endian_flag": self.is_little_endian,
            "big_endian": self.is_big_endian,
            "byte_order": self.byte_order,
        }


@dataclass(frozen=True)
class ChannelDescription:
    label: int
    name: str
    flags: int
    x: float
    y: float
    z: float


@dataclass(frozen=True)
class ChannelLayout:
    layout_tag: int
    bitmap: int
    descriptions: tuple[ChannelDescription, ...]
    channel_names: tuple[str, ...]
    layout_name: str
    source: str

    def as_dict(self) -> dict:
        return {
            "layout_tag": f"0x{self.layout_tag:08x}",
            "layout_name": self.layout_name,
            "source": self.source,
            "channel_bitmap": f"0x{self.bitmap:08x}",
            "channels": list(self.channel_names),
            "descriptions": [
                {
                    "label": item.label,
                    "name": item.name,
                    "flags": item.flags,
                    "x": item.x,
                    "y": item.y,
                    "z": item.z,
                }
                for item in self.descriptions
            ],
        }


@dataclass(frozen=True)
class PacketTable:
    number_packets: int
    number_valid_frames: int
    priming_frames: int
    remainder_frames: int

    def as_dict(self) -> dict:
        return {
            "number_packets": self.number_packets,
            "number_valid_frames": self.number_valid_frames,
            "priming_frames": self.priming_frames,
            "remainder_frames": self.remainder_frames,
        }


def _fourcc(raw: bytes) -> str:
    return raw.decode("latin-1")


def validate_big_endian_flag(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise InvalidEndianFlagError(
        "invalid big-endian flag: expected exactly 0 or 1, "
        f"got {value!r}; bit 1 of CAF linear-PCM flags is "
        "IsLittleEndian (0=big-endian, 1=little-endian)"
    )


def parse_description(payload: bytes) -> AudioDescription:
    if len(payload) < 32:
        raise CafFormatError(
            f"desc chunk is truncated: expected 32 bytes, got {len(payload)}"
        )
    sample_rate_bytes = payload[:8]
    sample_rate = struct.unpack(">d", sample_rate_bytes)[0]
    format_id = _fourcc(payload[8:12])
    flags, packet_bytes, frames_per_packet, channels, bits = struct.unpack(
        ">IIIII", payload[12:32]
    )
    if not math.isfinite(sample_rate) or sample_rate <= 0:
        raise CafFormatError(f"invalid CAF sample rate: {sample_rate!r}")
    if format_id == FORMAT_LINEAR_PCM.decode("latin-1"):
        unknown = flags & ~KNOWN_LPCM_FLAGS
        if unknown:
            raise InvalidEndianFlagError(
                f"invalid linear-PCM format flags 0x{flags:08x}: "
                f"reserved bits 0x{unknown:08x} are set; cannot determine "
                "the big-endian flag (bit 1: 0=big-endian, 1=little-endian)"
            )
        if channels <= 0:
            raise CafFormatError("linear PCM has zero channels")
        if bits <= 0:
            raise CafFormatError("linear PCM has zero bits per channel")
        if packet_bytes <= 0:
            raise CafFormatError("linear PCM has zero bytes per packet")
        if frames_per_packet != 1:
            raise UnsupportedFormatError(
                "linear PCM with framesPerPacket != 1 is unsupported"
            )
    return AudioDescription(
        sample_rate=sample_rate,
        format_id=format_id,
        format_flags=flags,
        bytes_per_packet=packet_bytes,
        frames_per_packet=frames_per_packet,
        channels_per_frame=channels,
        bits_per_channel=bits,
        sample_rate_bytes=sample_rate_bytes,
    )


def _bitmap_names(bitmap: int) -> list[str]:
    return [
        name for bit, name in enumerate(BITMAP_CHANNELS) if bitmap & (1 << bit)
    ]


def parse_channel_layout(
    payload: bytes, channels_per_frame: int
) -> ChannelLayout:
    if len(payload) < 12:
        raise CafFormatError(
            f"chan chunk is truncated: expected at least 12 bytes, "
            f"got {len(payload)}"
        )
    tag, bitmap, count = struct.unpack(">III", payload[:12])
    descriptions: list[ChannelDescription] = []
    required = 12 + count * 20
    if len(payload) < required:
        raise CafFormatError(
            f"chan chunk declares {count} descriptions but is truncated"
        )
    for index in range(count):
        offset = 12 + index * 20
        label, flags, x, y, z = struct.unpack(
            ">IIfff", payload[offset : offset + 20]
        )
        descriptions.append(
            ChannelDescription(
                label=label,
                name=CHANNEL_LABELS.get(label, f"Channel Label {label}"),
                flags=flags,
                x=x,
                y=y,
                z=z,
            )
        )

    if tag == LAYOUT_USE_DESCRIPTIONS:
        names = [item.name for item in descriptions]
        if len(names) != channels_per_frame:
            raise CafFormatError(
                f"chan uses {len(names)} descriptions for "
                f"{channels_per_frame} channels"
            )
        return ChannelLayout(
            tag, bitmap, tuple(descriptions), tuple(names), "Custom", "descriptions"
        )
    if tag == LAYOUT_USE_BITMAP:
        names = _bitmap_names(bitmap)
        if len(names) != channels_per_frame:
            raise CafFormatError(
                f"chan bitmap describes {len(names)} channels for "
                f"{channels_per_frame} channels"
            )
        return ChannelLayout(
            tag, bitmap, tuple(descriptions), tuple(names), "Bitmap", "bitmap"
        )
    known = LAYOUT_TAGS.get(tag)
    if known is not None:
        name, layout_channels = known
        if len(layout_channels) != channels_per_frame:
            raise CafFormatError(
                f"layout tag {name} describes {len(layout_channels)} channels "
                f"for {channels_per_frame} channels"
            )
        return ChannelLayout(
            tag, bitmap, tuple(descriptions), tuple(layout_channels), name, "tag"
        )
    declared = tag & 0xFFFF
    if declared != channels_per_frame:
        raise CafFormatError(
            f"layout tag 0x{tag:08x} declares {declared} channels for "
            f"{channels_per_frame} channels"
        )
    names = tuple(f"Channel {index + 1}" for index in range(channels_per_frame))
    return ChannelLayout(
        tag,
        bitmap,
        tuple(descriptions),
        names,
        f"Unknown 0x{tag:08x}",
        "tag",
    )


def default_channel_layout(channels: int) -> ChannelLayout:
    if channels == 1:
        names = ("Mono",)
        name = "Mono"
    elif channels == 2:
        names = ("Left", "Right")
        name = "Stereo"
    else:
        names = tuple(f"Channel {index + 1}" for index in range(channels))
        name = f"{channels}-channel"
    return ChannelLayout(0, 0, (), names, name, "default")


def parse_packet_table(payload: bytes) -> PacketTable:
    if len(payload) < 24:
        raise CafFormatError(
            f"pakt chunk is truncated: expected at least 24 bytes, "
            f"got {len(payload)}"
        )
    packets, valid_frames, priming, remainder = struct.unpack(
        ">qqii", payload[:24]
    )
    if packets < 0 or valid_frames < 0:
        raise CafFormatError("pakt packet/frame counts must be non-negative")
    return PacketTable(packets, valid_frames, priming, remainder)


class PCMDecoder:
    def __init__(self, description: AudioDescription):
        if not description.is_linear_pcm:
            raise UnsupportedFormatError(
                f"sample decoding requires lpcm, got {description.format_id!r}"
            )
        unknown = description.format_flags & ~KNOWN_LPCM_FLAGS
        if unknown:
            raise InvalidEndianFlagError(
                f"invalid linear-PCM format flags "
                f"0x{description.format_flags:08x}: reserved bits are set; "
                "big-endian flag at bit 1 must be 0 or 1"
            )
        if description.channels_per_frame <= 0:
            raise CafFormatError("linear PCM has zero channels")
        if description.bytes_per_packet <= 0:
            raise CafFormatError("linear PCM has zero bytes per packet")
        if description.bytes_per_packet % description.channels_per_frame:
            raise CafFormatError(
                "linear-PCM bytesPerPacket is not divisible by channels"
            )
        self.description = description
        self.channels = description.channels_per_frame
        self.bytes_per_sample = description.bytes_per_sample
        self.frame_bytes = description.bytes_per_packet

    def _decode_integer(self, raw: bytes) -> int:
        bits = self.description.bits_per_channel
        container_bits = self.bytes_per_sample * 8
        if bits > container_bits:
            raise CafFormatError(
                f"{bits}-bit sample does not fit in {self.bytes_per_sample} bytes"
            )
        value = int.from_bytes(
            raw, byteorder=self.description.byte_order, signed=True
        )
        if container_bits > bits:
            value >>= container_bits - bits
        return value

    def _decode_float(self, raw: bytes) -> float:
        prefix = "<" if self.description.is_little_endian else ">"
        bits = self.description.bits_per_channel
        if bits == 32 and self.bytes_per_sample == 4:
            return struct.unpack(prefix + "f", raw)[0]
        if bits == 64 and self.bytes_per_sample == 8:
            return struct.unpack(prefix + "d", raw)[0]
        raise UnsupportedFormatError(
            f"floating-point samples must be 32/64-bit, got {bits}-bit "
            f"in {self.bytes_per_sample} bytes"
        )

    def decode_sample(self, raw: bytes) -> int | float:
        if len(raw) != self.bytes_per_sample:
            raise CafFormatError(
                f"sample needs {self.bytes_per_sample} bytes, got {len(raw)}"
            )
        if self.description.is_float:
            return self._decode_float(raw)
        return self._decode_integer(raw)

    def decode_frame(self, raw: bytes) -> tuple[int | float, ...]:
        if len(raw) != self.frame_bytes:
            raise CafFormatError(
                f"frame needs {self.frame_bytes} bytes, got {len(raw)}"
            )
        return tuple(
            self.decode_sample(
                raw[offset : offset + self.bytes_per_sample]
            )
            for offset in range(0, self.frame_bytes, self.bytes_per_sample)
        )


class CAFFile:
    def __init__(
        self,
        source: str | os.PathLike[str] | bytes,
        *,
        name: str | None = None,
    ):
        self._path: Path | None = None
        self._blob: bytes | None = None
        if isinstance(source, (str, os.PathLike)):
            self._path = Path(source)
            self.name = name or str(self._path)
        elif isinstance(source, bytes):
            self._blob = source
            self.name = name or "<bytes>"
        else:
            raise TypeError("source must be a path or bytes")
        self.chunks: list[Chunk] = []
        self.description: AudioDescription | None = None
        self.packet_table: PacketTable | None = None
        self.channel_layout: ChannelLayout | None = None
        self.data_chunk: Chunk | None = None
        self.edit_count: int | None = None
        self.file_version = 0
        self.file_flags = 0
        self.warnings: list[str] = []
        self._parse()

    @property
    def file_size(self) -> int:
        if self._blob is not None:
            return len(self._blob)
        assert self._path is not None
        return self._path.stat().st_size

    def _open(self) -> BinaryIO:
        if self._blob is not None:
            return io.BytesIO(self._blob)
        assert self._path is not None
        return self._path.open("rb")

    def _read_at(self, offset: int, size: int) -> bytes:
        with self._open() as stream:
            stream.seek(offset)
            payload = stream.read(size)
        if len(payload) != size:
            raise CafFormatError(
                f"file ended at {offset + len(payload)}, "
                f"needed {size} bytes at offset {offset}"
            )
        return payload

    def _parse(self) -> None:
        with self._open() as stream:
            header = stream.read(8)
            if len(header) != 8:
                raise CafFormatError("file is too short for a CAF header")
            file_type, self.file_version, self.file_flags = struct.unpack(
                ">4sHH", header
            )
            if file_type != CAF_FILE_TYPE:
                raise CafFormatError(
                    f"not a CAF file: expected 'caff', got {file_type!r}"
                )
            if self.file_version != CAF_VERSION:
                self.warnings.append(
                    f"CAF version {self.file_version} is newer than 1"
                )
            offset = 8
            total_size = self.file_size
            while offset < total_size:
                if total_size - offset < 12:
                    trailing = self._read_at(offset, total_size - offset)
                    if trailing.strip(b"\x00"):
                        raise CafFormatError(
                            f"trailing {len(trailing)} bytes are too short "
                            "for a chunk header"
                        )
                    break
                raw_header = stream.read(12)
                if len(raw_header) != 12:
                    raise CafFormatError("truncated chunk header")
                chunk_type_raw, size = struct.unpack(">4sq", raw_header)
                chunk_type = _fourcc(chunk_type_raw)
                data_offset = offset + 12
                if size == -1:
                    if chunk_type != "data":
                        raise CafFormatError(
                            f"only the data chunk may use size -1, "
                            f"got {chunk_type!r}"
                        )
                    size = total_size - data_offset
                if size < 0:
                    raise CafFormatError(
                        f"chunk {chunk_type!r} has negative size {size}"
                    )
                end_offset = data_offset + size
                if end_offset > total_size:
                    raise CafFormatError(
                        f"chunk {chunk_type!r} at {offset} extends past EOF"
                    )
                payload = b""
                if chunk_type in {"desc", "chan", "pakt"}:
                    stream.seek(data_offset)
                    payload = stream.read(size)
                    if len(payload) != size:
                        raise CafFormatError(
                            f"chunk {chunk_type!r} is truncated"
                        )
                elif chunk_type == "data":
                    if size < 4:
                        raise CafFormatError(
                            "data chunk is too short for its edit count"
                        )
                    stream.seek(data_offset)
                    self.edit_count = struct.unpack(">I", stream.read(4))[0]
                self.chunks.append(
                    Chunk(
                        chunk_type,
                        size,
                        offset,
                        data_offset,
                        end_offset,
                        payload,
                    )
                )
                stream.seek(end_offset)
                offset = end_offset

        self._resolve_chunks()

    def _resolve_chunks(self) -> None:
        by_type: dict[str, list[Chunk]] = {}
        for chunk in self.chunks:
            by_type.setdefault(chunk.chunk_type, []).append(chunk)
        for required in ("desc", "data"):
            if required not in by_type:
                raise MissingChunkError(
                    f"required {required!r} chunk is missing; "
                    f"found {[chunk.chunk_type for chunk in self.chunks]}"
                )
        for singleton in ("desc", "data", "chan", "pakt"):
            if len(by_type.get(singleton, [])) > 1:
                raise CafFormatError(
                    f"chunk {singleton!r} appears more than once"
                )

        self.description = parse_description(by_type["desc"][0].payload)
        self.data_chunk = by_type["data"][0]
        if by_type["desc"][0].header_offset != 8:
            self.warnings.append(
                "desc chunk is not the first chunk; parsed by offset scan"
            )
        if (
            self.data_chunk.header_offset
            < by_type["desc"][0].header_offset
        ):
            self.warnings.append(
                "data chunk precedes desc chunk; sample offset was resolved "
                "after scanning the full chunk list"
            )
        if "pakt" in by_type:
            self.packet_table = parse_packet_table(by_type["pakt"][0].payload)
        if "chan" in by_type:
            self.channel_layout = parse_channel_layout(
                by_type["chan"][0].payload,
                self.description.channels_per_frame,
            )
        else:
            self.channel_layout = default_channel_layout(
                self.description.channels_per_frame
            )
        self._validate_frame_counts()

    @property
    def data_start(self) -> int:
        if self.data_chunk is None:
            raise MissingChunkError("data chunk is missing")
        return self.data_chunk.data_offset + 4

    @property
    def audio_bytes(self) -> int:
        if self.data_chunk is None:
            return 0
        return self.data_chunk.size - 4

    @property
    def container_frame_count(self) -> int:
        assert self.description is not None
        if self.description.bytes_per_packet == 0:
            return 0
        packets = self.audio_bytes // self.description.bytes_per_packet
        return packets * self.description.frames_per_packet

    @property
    def frame_count(self) -> int:
        if self.packet_table is not None:
            return self.packet_table.number_valid_frames
        return self.container_frame_count

    def _validate_frame_counts(self) -> None:
        assert self.description is not None
        if self.description.bytes_per_packet == 0:
            return
        partial = self.audio_bytes % self.description.bytes_per_packet
        if partial:
            self.warnings.append(
                f"data chunk ends with {partial} partial packet byte(s)"
            )
        if self.container_frame_count > self.frame_count:
            self.warnings.append(
                f"pakt valid frames {self.frame_count} are fewer than "
                f"the {self.container_frame_count} frames in data"
            )
        if self.container_frame_count < self.frame_count:
            raise CafFormatError(
                f"pakt claims {self.frame_count} frames but data only "
                f"contains {self.container_frame_count}"
            )

    def decoder(self) -> PCMDecoder:
        assert self.description is not None
        return PCMDecoder(self.description)

    def assert_endianness(self, expected: str) -> None:
        assert self.description is not None
        if not self.description.is_linear_pcm:
            raise UnsupportedFormatError(
                "endianness is only defined for linear PCM audio"
            )
        normalized = expected.lower()
        if normalized not in {"big", "little"}:
            raise ValueError("expected endianness must be 'big' or 'little'")
        actual = self.description.byte_order
        if actual != normalized:
            raise InvalidEndianFlagError(
                f"big-endian flag mismatch: CAF header says {actual}-endian "
                f"but {normalized}-endian was required "
                "(bit 1: 0=big-endian, 1=little-endian)"
            )

    def iter_frames(
        self, limit: int | None = None
    ) -> Iterator[tuple[int | float, ...]]:
        decoder = self.decoder()
        count = self.frame_count if limit is None else min(limit, self.frame_count)
        if count < 0:
            raise ValueError("limit must be non-negative")
        with self._open() as stream:
            stream.seek(self.data_start)
            for _ in range(count):
                raw = stream.read(decoder.frame_bytes)
                if len(raw) != decoder.frame_bytes:
                    raise CafFormatError("data chunk ended inside a frame")
                yield decoder.decode_frame(raw)

    def read_frames(
        self, limit: int | None = None
    ) -> list[tuple[int | float, ...]]:
        return list(self.iter_frames(limit))

    def sample_frame_offset(
        self, frame_index: int, channel_index: int | None = None
    ) -> dict:
        decoder = self.decoder()
        if not 0 <= frame_index < self.frame_count:
            raise IndexError(
                f"frame {frame_index} is outside 0..{self.frame_count - 1}"
            )
        if channel_index is not None and not 0 <= channel_index < decoder.channels:
            raise IndexError(
                f"channel {channel_index} is outside 0..{decoder.channels - 1}"
            )
        frame_offset = self.data_start + frame_index * decoder.frame_bytes
        channels = []
        for index in range(decoder.channels):
            if channel_index is not None and index != channel_index:
                continue
            channels.append(
                {
                    "index": index,
                    "name": self.channel_layout.channel_names[index],
                    "file_offset": frame_offset
                    + index * decoder.bytes_per_sample,
                }
            )
        return {
            "frame": frame_index,
            "file_offset": frame_offset,
            "data_relative_offset": frame_index * decoder.frame_bytes,
            "channels": channels,
        }

    def analysis(self, sample_limit: int = 4) -> dict:
        assert self.description is not None
        assert self.channel_layout is not None
        sample_rows = []
        sample_error = None
        try:
            decoder = self.decoder()
        except UnsupportedFormatError as exc:
            decoder = None
            sample_error = str(exc)
        if decoder is not None:
            with self._open() as stream:
                stream.seek(self.data_start)
                for frame_index in range(min(sample_limit, self.frame_count)):
                    raw = stream.read(decoder.frame_bytes)
                    decoded = decoder.decode_frame(raw)
                    sample_rows.append(
                        {
                            "frame": frame_index,
                            "raw_hex": raw.hex(),
                            "samples": list(decoded),
                            "offset": self.sample_frame_offset(frame_index),
                        }
                    )
        return {
            "file": self.name,
            "file_size": self.file_size,
            "header": {
                "file_type": "caff",
                "version": self.file_version,
                "flags": self.file_flags,
            },
            "chunk_order": [chunk.chunk_type for chunk in self.chunks],
            "chunks": [
                {
                    "type": chunk.chunk_type,
                    "size": chunk.size,
                    "header_offset": chunk.header_offset,
                    "data_offset": chunk.data_offset,
                    "end_offset": chunk.end_offset,
                }
                for chunk in self.chunks
            ],
            "audio_description": self.description.as_dict(),
            "packet_table": (
                None if self.packet_table is None else self.packet_table.as_dict()
            ),
            "frame_count": self.frame_count,
            "container_frame_count": self.container_frame_count,
            "audio_data": {
                "edit_count": self.edit_count,
                "payload_offset": self.data_start,
                "audio_bytes": self.audio_bytes,
            },
            "channel_layout": self.channel_layout.as_dict(),
            "sample_frames": sample_rows,
            "sample_extraction_error": sample_error,
            "warnings": list(self.warnings),
        }
