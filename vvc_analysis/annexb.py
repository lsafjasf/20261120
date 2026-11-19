"""Annex B scanning and RBSP helpers for H.266/VVC."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Iterator


class BitstreamError(ValueError):
    """Raised when a bitstream is malformed or outside the supported syntax."""


NAL_TYPE_NAMES = {
    0: "TRAIL_NUT",
    1: "STSA_NUT",
    2: "RADL_NUT",
    3: "RASL_NUT",
    4: "IDR_W_RADL",
    5: "IDR_N_LP",
    6: "CRA_NUT",
    7: "GDR_NUT",
    8: "RSV_IRAP_8",
    9: "RSV_IRAP_9",
    10: "RSV_VCL_10",
    11: "RSV_VCL_11",
    12: "OPI_NUT",
    13: "DCI_NUT",
    14: "VPS_NUT",
    15: "SPS_NUT",
    16: "PPS_NUT",
    17: "PREFIX_APS_NUT",
    18: "SUFFIX_APS_NUT",
    19: "PH_NUT",
    20: "AUD_NUT",
    21: "EOS_NUT",
    22: "EOB_NUT",
    23: "PREFIX_SEI_NUT",
    24: "SUFFIX_SEI_NUT",
    25: "FD_NUT",
    26: "RSV_NVCL_26",
    27: "RSV_NVCL_27",
    28: "UNSPEC_28",
    29: "UNSPEC_29",
    30: "UNSPEC_30",
    31: "UNSPEC_31",
}

KNOWN_NAL_TYPES = frozenset(range(26))


@dataclass(frozen=True)
class NalUnit:
    """One Annex B NAL unit, including its original header and payload bytes."""

    index: int
    start_offset: int
    prefix_length: int
    nal_offset: int
    end_offset: int
    nal_type: int
    layer_id: int
    temporal_id: int
    forbidden_zero_bit: int
    reserved_zero_bit: int
    raw: bytes

    @property
    def type_name(self) -> str:
        return NAL_TYPE_NAMES.get(self.nal_type, f"UNKNOWN_{self.nal_type}")

    @property
    def is_unknown(self) -> bool:
        return self.nal_type not in KNOWN_NAL_TYPES

    @property
    def payload(self) -> bytes:
        return self.raw[2:]

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.raw).hexdigest()

    def summary(self) -> dict:
        return {
            "index": self.index,
            "start_offset": self.start_offset,
            "start_code_hex": "00000001" if self.prefix_length == 4 else "000001",
            "prefix_length": self.prefix_length,
            "nal_offset": self.nal_offset,
            "end_offset": self.end_offset,
            "size": len(self.raw),
            "nal_type": self.nal_type,
            "type_name": self.type_name,
            "layer_id": self.layer_id,
            "temporal_id": self.temporal_id,
            "sha256": self.sha256,
        }


def _start_code_offsets(data: bytes) -> list[tuple[int, int, int]]:
    """Return (prefix_start, prefix_length, payload_start) triples."""
    found: list[tuple[int, int, int]] = []
    search_from = 0
    while True:
        marker = data.find(b"\x00\x00\x01", search_from)
        if marker < 0:
            break
        if marker > 0 and data[marker - 1] == 0:
            prefix_start = marker - 1
            prefix_length = 4
        else:
            prefix_start = marker
            prefix_length = 3
        found.append((prefix_start, prefix_length, marker + 3))
        search_from = marker + 3
    return found


def iter_nal_units(data: bytes) -> Iterator[NalUnit]:
    """Split an Annex B byte stream into NAL units without dropping bytes."""
    starts = _start_code_offsets(data)
    for index, (start_offset, prefix_length, nal_offset) in enumerate(starts):
        end_offset = starts[index + 1][0] if index + 1 < len(starts) else len(data)
        raw = data[nal_offset:end_offset]
        if len(raw) < 2:
            raise BitstreamError(
                f"NAL {index} at byte {nal_offset} has no complete two-byte header"
            )
        first, second = raw[0], raw[1]
        yield NalUnit(
            index=index,
            start_offset=start_offset,
            prefix_length=prefix_length,
            nal_offset=nal_offset,
            end_offset=end_offset,
            nal_type=(second >> 3) & 0x1F,
            layer_id=((first & 0x3F) << 0),
            temporal_id=(second & 0x07) - 1,
            forbidden_zero_bit=(first >> 7) & 1,
            reserved_zero_bit=(first >> 6) & 1,
            raw=raw,
        )


def rbsp_from_ebsp(ebsp: bytes) -> bytes:
    """Remove emulation_prevention_three_byte values from a NAL payload."""
    out = bytearray()
    zeros = 0
    for value in ebsp:
        if zeros >= 2 and value == 0x03:
            zeros = 0
            continue
        out.append(value)
        zeros = zeros + 1 if value == 0 else 0
    return bytes(out)


class BitReader:
    """MSB-first bounded bit reader."""

    def __init__(self, data: bytes):
        self.data = data
        self.bitpos = 0

    @property
    def bits_left(self) -> int:
        return len(self.data) * 8 - self.bitpos

    def read_bits(self, count: int) -> int:
        if count < 0 or count > 32:
            raise BitstreamError(f"invalid bit count {count}")
        if self.bits_left < count:
            raise BitstreamError(
                f"need {count} bits at bit {self.bitpos}, only {self.bits_left} remain"
            )
        value = 0
        for _ in range(count):
            byte = self.data[self.bitpos >> 3]
            shift = 7 - (self.bitpos & 7)
            value = (value << 1) | ((byte >> shift) & 1)
            self.bitpos += 1
        return value

    def read_flag(self) -> int:
        return self.read_bits(1)

    def read_ue(self) -> int:
        leading_zeros = 0
        while self.read_flag() == 0:
            leading_zeros += 1
            if leading_zeros > 31:
                raise BitstreamError("Exp-Golomb value exceeds 32 bits")
        if leading_zeros == 0:
            return 0
        return (1 << leading_zeros) - 1 + self.read_bits(leading_zeros)

    def read_se(self) -> int:
        code_num = self.read_ue()
        return (code_num + 1) // 2 if code_num & 1 else -(code_num // 2)

    def align_zero(self) -> None:
        while self.bitpos & 7:
            if self.read_flag() != 0:
                raise BitstreamError(f"non-zero alignment bit at {self.bitpos - 1}")


def preserve_unknown_nal(nal: NalUnit, output_dir: Path) -> dict:
    """Write one unknown NAL verbatim and return its manifest entry."""
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"nal_{nal.index:06d}_type_{nal.nal_type:02d}.bin"
    path = output_dir / filename
    path.write_bytes(nal.raw)
    return {
        **nal.summary(),
        "file": str(path),
        "raw_hex": nal.raw.hex(),
    }
