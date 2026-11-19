"""Strict, offset-aware AIFF and AIFC chunk parsing."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil
from pathlib import Path

from .extended80 import decode_extended80


@dataclass
class Chunk:
    chunk_id: str
    header_offset: int
    data_offset: int
    size: int
    padded_end: int

    def as_dict(self) -> dict:
        return {
            "id": self.chunk_id,
            "header_offset": self.header_offset,
            "data_offset": self.data_offset,
            "size": self.size,
            "padded_end": self.padded_end,
        }


@dataclass
class CommInfo:
    channels: int
    num_sample_frames: int
    sample_size_bits: int
    sample_rate: float
    sample_rate_info: dict
    compression_type: str
    compression_name: str
    chunk_offset: int
    chunk_size: int

    @property
    def duration_seconds(self) -> float:
        return self.num_sample_frames / self.sample_rate if self.sample_rate else 0.0

    def as_dict(self) -> dict:
        return {
            "channels": self.channels,
            "num_sample_frames": self.num_sample_frames,
            "sample_size_bits": self.sample_size_bits,
            "sample_rate": self.sample_rate,
            "sample_rate_hex": float(self.sample_rate).hex(),
            "sample_rate_raw_hex": self.sample_rate_info["raw_hex"],
            "sample_rate_extended": self.sample_rate_info,
            "compression_type": self.compression_type,
            "compression_name": self.compression_name,
            "duration_seconds": self.duration_seconds,
            "chunk_offset": self.chunk_offset,
            "chunk_size": self.chunk_size,
        }


@dataclass
class SsndInfo:
    offset: int
    block_size: int
    chunk_offset: int
    chunk_size: int
    data_offset: int

    @property
    def audio_start_absolute(self) -> int:
        return self.data_offset + 8 + self.offset

    @property
    def audio_bytes(self) -> int:
        return max(0, self.chunk_size - 8 - self.offset)

    def as_dict(self, file_size: int) -> dict:
        return {
            "offset": self.offset,
            "block_size": self.block_size,
            "data_offset": self.audio_start_absolute,
            "data_size": self.audio_bytes,
            "chunk_offset": self.chunk_offset,
            "chunk_size": self.chunk_size,
            "file_size": file_size,
        }


@dataclass(frozen=True)
class CodecLayout:
    family: str
    description: str
    endian: str | None
    bytes_per_sample: float
    packet_frames: int = 1
    packet_bytes_per_channel: int | None = None
    reversible: bool = True

    def as_dict(self, compression_type: str) -> dict:
        return {
            "compression_type": compression_type,
            "family": self.family,
            "description": self.description,
            "endian": self.endian,
            "bytes_per_sample": self.bytes_per_sample,
            "packet_frames": self.packet_frames,
            "packet_bytes_per_channel": self.packet_bytes_per_channel,
            "reversible": self.reversible,
            "supported": True,
        }


LAYOUTS = {
    "NONE": CodecLayout("pcm_be", "PCM, two's complement, big-endian", "big", 0.0),
    "sowt": CodecLayout("pcm_le", "PCM, two's complement, little-endian", "little", 0.0),
    "raw ": CodecLayout("pcm_u8", "PCM, unsigned 8-bit", None, 1.0),
    "fl32": CodecLayout("float_be", "32-bit IEEE float, big-endian", "big", 4.0),
    "FL32": CodecLayout("float_be", "32-bit IEEE float, big-endian", "big", 4.0),
    "fl64": CodecLayout("float_be", "64-bit IEEE float, big-endian", "big", 8.0),
    "FL64": CodecLayout("float_be", "64-bit IEEE float, big-endian", "big", 8.0),
    "ulaw": CodecLayout("g711", "uLaw 2:1 compression", "big", 1.0),
    "ULAW": CodecLayout("g711", "uLaw 2:1 compression", "big", 1.0),
    "alaw": CodecLayout("g711", "aLaw 2:1 compression", "big", 1.0),
    "ALAW": CodecLayout("g711", "aLaw 2:1 compression", "big", 1.0),
    "ima4": CodecLayout(
        "ima4",
        "IMA 4:1 ADPCM",
        "big",
        34 / 64,
        packet_frames=64,
        packet_bytes_per_channel=34,
    ),
}


@dataclass
class AiffFile:
    path: Path
    data: bytes
    form_type: str
    form_end: int
    chunks: list[Chunk] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    comm: CommInfo | None = None
    ssnd: SsndInfo | None = None
    layout: CodecLayout | None = None
    layout_error: str | None = None

    def _require(self) -> tuple[CommInfo, SsndInfo, CodecLayout]:
        if self.comm is None or self.ssnd is None or self.layout is None:
            detail = f": {self.layout_error}" if self.layout_error else ""
            raise ValueError(f"file is missing usable COMM/SSND layout{detail}")
        return self.comm, self.ssnd, self.layout

    def _pcm_byte_width(self) -> int:
        comm, _, _ = self._require()
        return max(1, (comm.sample_size_bits + 7) // 8)

    def _frame_bytes(self) -> int:
        _, _, layout = self._require()
        return int(layout.bytes_per_sample * self.comm.channels * layout.packet_frames)

    def expected_audio_bytes(self) -> int:
        comm, _, layout = self._require()
        if layout.family == "ima4":
            packets = ceil(comm.num_sample_frames / layout.packet_frames)
            return packets * layout.packet_bytes_per_channel * comm.channels
        return comm.num_sample_frames * comm.channels * self._pcm_byte_width()

    def sample_location(self, sample_frame: int, channel: int = 0) -> dict:
        comm, ssnd, layout = self._require()
        if not 0 <= sample_frame < comm.num_sample_frames:
            raise IndexError(f"sample frame {sample_frame} outside [0, {comm.num_sample_frames})")
        if not 0 <= channel < comm.channels:
            raise IndexError(f"channel {channel} outside [0, {comm.channels})")

        if layout.family == "ima4":
            packet = sample_frame // layout.packet_frames
            frame_in_packet = sample_frame % layout.packet_frames
            packet_bytes = layout.packet_bytes_per_channel
            relative = (
                packet * comm.channels * packet_bytes
                + channel * packet_bytes
                + 2
                + frame_in_packet // 2
            )
            nibble = frame_in_packet % 2
        else:
            packet = sample_frame
            frame_in_packet = 0
            relative = (sample_frame * comm.channels + channel) * self._pcm_byte_width()
            nibble = None

        return {
            "compression_type": comm.compression_type,
            "sample_frame": sample_frame,
            "channel": channel,
            "container_frame": packet,
            "frame_in_packet": frame_in_packet,
            "relative_byte_offset": relative,
            "absolute_file_offset": ssnd.audio_start_absolute + relative,
            "nibble": nibble,
        }

    def recover_location(
        self, relative_byte_offset: int, channel: int = 0, nibble: int | None = None
    ) -> list[int]:
        """Map a compressed byte position back to all candidate PCM frames."""
        comm, _, layout = self._require()
        if not 0 <= channel < comm.channels:
            raise IndexError(f"channel {channel} outside [0, {comm.channels})")
        if relative_byte_offset < 0:
            raise IndexError("relative_byte_offset must be non-negative")

        if layout.family == "ima4":
            packet_bytes = layout.packet_bytes_per_channel
            packet_total = packet_bytes * comm.channels
            packet, within_packet = divmod(relative_byte_offset, packet_total)
            packet_channel, within_channel = divmod(within_packet, packet_bytes)
            if packet_channel != channel or within_channel < 2:
                return []
            first_in_packet = (within_channel - 2) * 2
            candidates = [
                packet * layout.packet_frames + first_in_packet + bit
                for bit in (0, 1)
                if first_in_packet + bit < layout.packet_frames
            ]
            if nibble in (0, 1):
                candidate = packet * layout.packet_frames + first_in_packet + nibble
                return [candidate] if candidate < comm.num_sample_frames else []
            return [frame for frame in candidates if frame < comm.num_sample_frames]

        frame_bytes = self._pcm_byte_width() * comm.channels
        container_frame, channel_byte = divmod(relative_byte_offset, frame_bytes)
        if container_frame >= comm.num_sample_frames:
            return []
        byte_width = self._pcm_byte_width()
        if channel_byte // byte_width != channel or channel_byte % byte_width:
            return []
        return [container_frame]

    def layout_dict(self) -> dict:
        comm, _, layout = self._require()
        result = layout.as_dict(comm.compression_type)
        if layout.family in ("pcm_be", "pcm_le"):
            result["bytes_per_sample"] = float(self._pcm_byte_width())
            result["packet_bytes_per_channel"] = self._pcm_byte_width()
        return result

    def validation_dict(self) -> dict:
        comm, ssnd, layout = self._require()
        expected = self.expected_audio_bytes()
        packets = (
            ceil(comm.num_sample_frames / layout.packet_frames)
            if layout.family == "ima4"
            else comm.num_sample_frames
        )
        return {
            "expected_audio_bytes": expected,
            "available_audio_bytes": ssnd.audio_bytes,
            "extra_bytes_after_expected_payload": max(0, ssnd.audio_bytes - expected),
            "payload_complete": ssnd.audio_bytes >= expected,
            "expected_packets": packets,
            "packet_frames": layout.packet_frames,
            "packet_bytes_per_channel": (
                layout.packet_bytes_per_channel
                if layout.packet_bytes_per_channel is not None
                else self._pcm_byte_width()
            ),
            "bytes_per_pcm_frame_all_channels": (
                expected / comm.num_sample_frames if comm.num_sample_frames else 0.0
            ),
        }

    def byte_ranges(self) -> dict:
        comm, ssnd, layout = self._require()
        first = self.sample_location(0, 0)
        last = self.sample_location(comm.num_sample_frames - 1, 0)
        return {
            "ssnd_offset": ssnd.offset,
            "ssnd_block_size": ssnd.block_size,
            "audio_start_absolute": ssnd.audio_start_absolute,
            "audio_end_absolute": ssnd.audio_start_absolute + ssnd.audio_bytes,
            "first_sample_absolute": first["absolute_file_offset"],
            "first_sample_relative": first["relative_byte_offset"],
            "last_sample_absolute": last["absolute_file_offset"],
            "last_sample_relative": last["relative_byte_offset"],
        }

    def as_dict(self) -> dict:
        payload = {
            "source": str(self.path),
            "form_type": self.form_type,
            "file_size": len(self.data),
            "form_end": self.form_end,
            "chunks": [chunk.as_dict() for chunk in self.chunks],
            "warnings": self.warnings,
            "comm": None,
            "ssnd": None,
            "layout": None,
            "layout_error": self.layout_error,
        }
        if self.comm:
            payload["comm"] = self.comm.as_dict()
            payload["sample_rate_80bit"] = self.comm.sample_rate_info
        if self.ssnd:
            payload["ssnd"] = self.ssnd.as_dict(len(self.data))
        if self.comm and self.ssnd and self.layout:
            payload["layout"] = self.layout_dict()
            payload["data_validation"] = self.validation_dict()
            payload["byte_ranges"] = self.byte_ranges()
        return payload


def _read_pstring(raw: bytes) -> str:
    if not raw:
        return ""
    length = raw[0]
    return raw[1 : 1 + length].decode("latin-1")


def _select_layout(aiff: AiffFile, compression_type: str) -> None:
    layout = LAYOUTS.get(compression_type)
    if layout is None:
        normalized = compression_type.lower()
        layout = LAYOUTS.get(normalized)
    if layout is None:
        aiff.layout_error = f"unsupported compression type {compression_type!r}"
        return
    aiff.layout = layout
    if layout.family in ("pcm_be", "pcm_le"):
        bits = aiff.comm.sample_size_bits
        if bits not in (8, 16, 24, 32):
            aiff.layout_error = f"unsupported PCM sample size {bits} bits"
            aiff.layout = None


def parse_aiff(path: str | Path) -> AiffFile:
    path = Path(path)
    data = path.read_bytes()
    if len(data) < 12 or data[:4] != b"FORM":
        raise ValueError(f"{path}: not an IFF FORM file")

    form_type = data[8:12].decode("latin-1")
    if form_type not in ("AIFF", "AIFC"):
        raise ValueError(f"{path}: unsupported FORM type {form_type!r}")
    form_size = int.from_bytes(data[4:8], "big")
    form_end = 8 + form_size
    aiff = AiffFile(path=path, data=data, form_type=form_type, form_end=form_end)
    if form_end > len(data):
        aiff.warnings.append(f"FORM ends at {form_end}, file has only {len(data)} bytes")

    position = 12
    scan_end = min(form_end, len(data))
    while position + 8 <= scan_end:
        chunk_id = data[position : position + 4].decode("latin-1")
        size = int.from_bytes(data[position + 4 : position + 8], "big")
        data_offset = position + 8
        padded_end = data_offset + size + (size & 1)
        chunk = Chunk(chunk_id, position, data_offset, size, padded_end)
        aiff.chunks.append(chunk)
        if data_offset + size > scan_end:
            aiff.warnings.append(f"{chunk_id} payload overruns FORM boundary")

        body = data[data_offset : min(data_offset + size, len(data))]
        if chunk_id == "COMM" and aiff.comm is None:
            if len(body) < 18:
                aiff.warnings.append("COMM is shorter than 18 bytes")
            else:
                channels = int.from_bytes(body[0:2], "big")
                frames = int.from_bytes(body[2:6], "big")
                bits = int.from_bytes(body[6:8], "big")
                rate_info = decode_extended80(body[8:18])
                compression_type = "NONE"
                compression_name = "NONE"
                if form_type == "AIFC":
                    if len(body) < 22:
                        aiff.warnings.append("AIFC COMM lacks compression type")
                    else:
                        compression_type = body[18:22].decode("latin-1")
                        compression_name = _read_pstring(body[22:])
                aiff.comm = CommInfo(
                    channels=channels,
                    num_sample_frames=frames,
                    sample_size_bits=bits,
                    sample_rate=float(rate_info["value"]),
                    sample_rate_info=rate_info,
                    compression_type=compression_type,
                    compression_name=compression_name,
                    chunk_offset=position,
                    chunk_size=size,
                )
        elif chunk_id == "SSND" and aiff.ssnd is None:
            if len(body) < 8:
                aiff.warnings.append("SSND is shorter than 8 bytes")
            else:
                aiff.ssnd = SsndInfo(
                    offset=int.from_bytes(body[0:4], "big"),
                    block_size=int.from_bytes(body[4:8], "big"),
                    chunk_offset=position,
                    chunk_size=size,
                    data_offset=data_offset,
                )
        position = padded_end

    if aiff.comm is None:
        aiff.warnings.append("missing COMM chunk")
    if aiff.ssnd is None:
        aiff.warnings.append("missing SSND chunk")
    if aiff.comm and aiff.comm.channels < 1:
        aiff.layout_error = "COMM channels must be at least one"
    elif aiff.comm:
        _select_layout(aiff, aiff.comm.compression_type)
    return aiff
