#!/usr/bin/env python3
"""Parse WavPack block headers, checksums, and recover after damaged blocks."""

from __future__ import annotations

import argparse
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence


MAGIC = b"wvpk"
HEADER_SIZE = 32
MAX_RESYNC_BYTES = 1024 * 1024

BYTES_STORED = 0x00000003
MONO_FLAG = 0x00000004
HYBRID_FLAG = 0x00000008
SHIFT_LSB = 13
SHIFT_MASK = 0x1F << SHIFT_LSB
MAG_LSB = 18
MAG_MASK = 0x1F << MAG_LSB
SRATE_LSB = 23
SRATE_MASK = 0x0F << SRATE_LSB
HAS_CHECKSUM = 0x10000000
FALSE_STEREO = 0x40000000

ID_UNIQUE = 0x3F
ID_ODD_SIZE = 0x40
ID_LARGE = 0x80
ID_BLOCK_CHECKSUM = 0x2F

SAMPLE_RATES = (
    6000, 8000, 9600, 11025, 12000, 16000, 22050, 24000,
    32000, 44100, 48000, 64000, 88200, 96000, 192000,
)


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


@dataclass(frozen=True)
class WavPackHeader:
    offset: int
    block_id: bytes
    ck_size: int
    version: int
    block_index_hi: int
    total_samples_hi: int
    total_samples_raw: int
    block_index_raw: int
    block_samples: int
    flags: int
    crc: int

    @property
    def block_index(self) -> int:
        return self.block_index_raw + (self.block_index_hi << 32)

    @property
    def total_samples(self) -> int | None:
        if self.total_samples_raw == 0xFFFFFFFF:
            return None
        return (
            self.total_samples_raw
            + (self.total_samples_hi << 32)
            - self.total_samples_hi
        )

    @property
    def declared_size(self) -> int:
        return self.ck_size + 8

    @property
    def declared_end(self) -> int:
        return self.offset + self.declared_size

    @property
    def sample_end(self) -> int:
        return self.block_index + self.block_samples

    @property
    def hybrid(self) -> bool:
        return bool(self.flags & HYBRID_FLAG)

    @property
    def mono_data(self) -> bool:
        return bool(self.flags & (MONO_FLAG | FALSE_STEREO))

    @property
    def mode(self) -> str:
        if self.hybrid:
            return "hybrid"
        if self.flags & MONO_FLAG:
            return "mono"
        return "stereo"

    @property
    def channels(self) -> int:
        return 1 if self.flags & MONO_FLAG else 2

    @property
    def bytes_per_sample(self) -> int:
        return (self.flags & BYTES_STORED) + 1

    @property
    def sample_rate(self) -> int | None:
        index = (self.flags & SRATE_MASK) >> SRATE_LSB
        return SAMPLE_RATES[index] if index < len(SAMPLE_RATES) else None

    @property
    def magnitude_bits(self) -> int:
        return (self.flags & MAG_MASK) >> MAG_LSB

    @property
    def shift_bits(self) -> int:
        return (self.flags & SHIFT_MASK) >> SHIFT_LSB

    @property
    def has_checksum(self) -> bool:
        return bool(self.flags & HAS_CHECKSUM)


def parse_header(data: bytes, offset: int) -> WavPackHeader:
    if offset < 0 or offset + HEADER_SIZE > len(data):
        raise ValueError(f"incomplete WavPack header at byte {offset}")
    if data[offset:offset + 4] != MAGIC:
        raise ValueError(f"missing wvpk id at byte {offset}")

    ck_size, version, index_hi, total_hi, total_raw, index_raw = struct.unpack_from(
        "<IHBBII", data, offset + 4
    )
    block_samples, flags, crc = struct.unpack_from("<III", data, offset + 20)
    return WavPackHeader(
        offset=offset,
        block_id=data[offset:offset + 4],
        ck_size=ck_size,
        version=version,
        block_index_hi=index_hi,
        total_samples_hi=total_hi,
        total_samples_raw=total_raw,
        block_index_raw=index_raw,
        block_samples=block_samples,
        flags=flags,
        crc=crc,
    )


def plausible_header(data: bytes, offset: int) -> bool:
    if offset < 0 or offset + HEADER_SIZE > len(data):
        return False
    if data[offset:offset + 4] != MAGIC:
        return False
    ck_size = _u32(data, offset + 4)
    version = struct.unpack_from("<H", data, offset + 8)[0]
    block_samples = _u32(data, offset + 20)
    return (
        ck_size >= 24
        and 0x402 <= version <= 0x410
        and block_samples <= 0x20000
    )


@dataclass
class MetadataCheck:
    valid: bool
    checksum_present: bool = False
    checksum_ok: bool | None = None
    stored_checksum: int | None = None
    calculated_checksum: int | None = None
    error: str | None = None


def _metadata_checksum(data: bytes, start: int, stop: int) -> int:
    checksum = 0xFFFFFFFF
    if (stop - start) & 1:
        raise ValueError("block checksum input must contain complete 16-bit words")
    for offset in range(start, stop, 2):
        checksum = (
            checksum * 3 + data[offset] + (data[offset + 1] << 8)
        ) & 0xFFFFFFFF
    return checksum


def verify_metadata_and_checksum(
    data: bytes, header: WavPackHeader
) -> MetadataCheck:
    end = header.declared_end
    if end > len(data):
        return MetadataCheck(False, error="declared block extends past end of file")
    if header.ck_size < HEADER_SIZE - 8:
        return MetadataCheck(False, error="ckSize is smaller than a WavPack header")

    offset = header.offset + HEADER_SIZE
    while offset < end:
        if end - offset < 2:
            return MetadataCheck(False, error="truncated metadata header")

        meta_id = data[offset]
        size = data[offset + 1] << 1
        header_bytes = 2
        if meta_id & ID_LARGE:
            if end - offset < 4:
                return MetadataCheck(False, error="truncated large metadata header")
            size += (data[offset + 2] << 9) + (data[offset + 3] << 17)
            header_bytes = 4

        payload = offset + header_bytes
        payload_end = payload + size
        if payload_end > end:
            return MetadataCheck(False, error="metadata payload exceeds block")

        if (meta_id & ID_UNIQUE) == ID_BLOCK_CHECKSUM:
            if meta_id & ID_ODD_SIZE or size not in (2, 4):
                return MetadataCheck(
                    False, checksum_present=True, checksum_ok=False,
                    error="invalid block checksum metadata size",
                )
            stored = int.from_bytes(data[payload:payload_end], "little")
            try:
                calculated = _metadata_checksum(data, header.offset, offset)
            except ValueError as exc:
                return MetadataCheck(
                    False, checksum_present=True, checksum_ok=False,
                    stored_checksum=stored, error=str(exc),
                )
            expected = calculated if size == 4 else (calculated ^ (calculated >> 16)) & 0xFFFF
            return MetadataCheck(
                True,
                checksum_present=True,
                checksum_ok=stored == expected,
                stored_checksum=stored,
                calculated_checksum=expected,
            )

        offset = payload_end

    if offset != end:
        return MetadataCheck(False, error="metadata does not exactly fill block")
    if header.has_checksum:
        return MetadataCheck(
            False, error="HAS_CHECKSUM is set but checksum metadata is missing"
        )
    return MetadataCheck(True)


def _read_sample(raw: bytes, offset: int, width: int) -> int:
    if width > 4:
        raise ValueError(f"unsupported sample width: {width}")
    return int.from_bytes(raw[offset:offset + width], "little", signed=True)


def calculate_header_crc(
    raw_pcm: bytes, header: WavPackHeader
) -> tuple[int, str | None]:
    width = header.bytes_per_sample
    physical_channels = 1 if header.flags & MONO_FLAG else 2
    frame_size = width * physical_channels
    first_frame = header.block_index
    final_frame = header.block_index + header.block_samples
    required = final_frame * frame_size
    if required > len(raw_pcm):
        return 0, f"oracle PCM ends before sample {final_frame}"

    crc = 0xFFFFFFFF
    if header.mono_data:
        for frame in range(first_frame, final_frame):
            sample = _read_sample(raw_pcm, frame * frame_size, width)
            crc = (crc * 3 + sample) & 0xFFFFFFFF
    else:
        for frame in range(first_frame, final_frame):
            offset = frame * frame_size
            left = _read_sample(raw_pcm, offset, width)
            right = _read_sample(raw_pcm, offset + width, width)
            crc = (crc * 9 + left * 3 + right) & 0xFFFFFFFF
    return crc, None


@dataclass
class BlockRecord:
    header: WavPackHeader
    metadata: MetadataCheck
    header_crc_calculated: int | None = None
    header_crc_ok: bool | None = None
    header_crc_error: str | None = None
    accepted: bool = False
    skip_reason: str | None = None
    resume_offset: int | None = None
    skipped_bytes: int | None = None

    @property
    def checksum_failed(self) -> bool:
        return (
            not self.metadata.valid
            or self.metadata.checksum_ok is False
            or self.header_crc_ok is False
        )


@dataclass
class ScanResult:
    path: str
    file_size: int
    records: list[BlockRecord] = field(default_factory=list)
    checksum_failures: int = 0
    skipped_blocks: int = 0
    trailing_bytes: int = 0
    declared_total_samples: int | None = None
    total_samples_consistent: bool = True

    @property
    def accepted_blocks(self) -> list[BlockRecord]:
        return [record for record in self.records if record.accepted]

    @property
    def recovered_sample_indices(self) -> list[int]:
        return [record.header.block_index for record in self.accepted_blocks]

    @property
    def recovered_sample_span(self) -> list[int] | None:
        if not self.accepted_blocks:
            return None
        return [
            min(record.header.block_index for record in self.accepted_blocks),
            max(record.header.sample_end for record in self.accepted_blocks),
        ]

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "file_size": self.file_size,
            "declared_total_samples": self.declared_total_samples,
            "total_samples_consistent": self.total_samples_consistent,
            "checksum_failures": self.checksum_failures,
            "skipped_blocks": self.skipped_blocks,
            "trailing_bytes": self.trailing_bytes,
            "recovered_sample_indices": self.recovered_sample_indices,
            "recovered_sample_span": self.recovered_sample_span,
            "blocks": [
                {
                    "byte_offset": record.header.offset,
                    "byte_offset_hex": f"0x{record.header.offset:X}",
                    "id": record.header.block_id.decode("ascii"),
                    "id_hex": f"0x{int.from_bytes(record.header.block_id, 'big'):08X}",
                    "ck_size": record.header.ck_size,
                    "flags": f"0x{record.header.flags:08X}",
                    "mode": record.header.mode,
                    "channels": record.header.channels,
                    "hybrid": record.header.hybrid,
                    "sample_rate": record.header.sample_rate,
                    "sample_index": record.header.block_index,
                    "sample_end": record.header.sample_end,
                    "block_samples": record.header.block_samples,
                    "crc": f"0x{record.header.crc:08X}",
                    "calculated_header_crc": (
                        f"0x{record.header_crc_calculated:08X}"
                        if record.header_crc_calculated is not None else None
                    ),
                    "header_crc_ok": record.header_crc_ok,
                    "metadata_checksum_present": record.metadata.checksum_present,
                    "metadata_checksum_ok": record.metadata.checksum_ok,
                    "metadata_error": record.metadata.error,
                    "accepted": record.accepted,
                    "skip_reason": record.skip_reason,
                    "resume_offset": record.resume_offset,
                    "resume_offset_hex": (
                        f"0x{record.resume_offset:X}"
                        if record.resume_offset is not None else None
                    ),
                    "skipped_bytes": record.skipped_bytes,
                }
                for record in self.records
            ],
        }


def find_next_header(data: bytes, start: int) -> int | None:
    search = max(0, start)
    limit = min(len(data), search + MAX_RESYNC_BYTES)
    while search < limit:
        candidate = data.find(MAGIC, search, limit)
        if candidate < 0:
            return None
        if plausible_header(data, candidate):
            header = parse_header(data, candidate)
            if header.declared_end <= len(data):
                return candidate
        search = candidate + 1
    return None


def scan_file(path: Path, oracle_pcm: bytes | None = None) -> ScanResult:
    data = path.read_bytes()
    result = ScanResult(path=str(path), file_size=len(data))
    cursor = 0

    while cursor < len(data):
        if data[cursor:cursor + 4] == MAGIC and cursor + HEADER_SIZE <= len(data):
            offset = cursor
        else:
            offset = find_next_header(data, cursor)
        if offset is None:
            result.trailing_bytes = len(data) - cursor
            break
        if offset > cursor and not result.records:
            result.trailing_bytes += offset - cursor

        header = parse_header(data, offset)
        if header.total_samples is not None:
            if result.declared_total_samples is None:
                result.declared_total_samples = header.total_samples
            elif result.declared_total_samples != header.total_samples:
                result.total_samples_consistent = False

        metadata = verify_metadata_and_checksum(data, header)
        record = BlockRecord(header=header, metadata=metadata)

        if oracle_pcm is not None:
            calculated, error = calculate_header_crc(oracle_pcm, header)
            record.header_crc_calculated = calculated
            record.header_crc_error = error
            record.header_crc_ok = error is None and calculated == header.crc

        record.accepted = (
            metadata.valid
            and metadata.checksum_ok is not False
            and record.header_crc_ok is not False
        )

        if record.accepted:
            cursor = header.declared_end
        else:
            if not metadata.valid:
                record.skip_reason = metadata.error or "invalid metadata"
            elif metadata.checksum_ok is False:
                record.skip_reason = "block metadata checksum mismatch"
            elif record.header_crc_ok is False:
                record.skip_reason = record.header_crc_error or "header CRC mismatch"
            else:
                record.skip_reason = "block rejected"

            resume = find_next_header(data, offset + 4)
            record.resume_offset = resume
            if resume is not None:
                record.skipped_bytes = resume - offset
                cursor = resume
            else:
                cursor = len(data)

        result.records.append(record)

    result.checksum_failures = sum(
        record.checksum_failed for record in result.records
    )
    result.skipped_blocks = sum(not record.accepted for record in result.records)
    return result


def _ok(value: bool | None) -> str:
    if value is None:
        return "n/a"
    return "OK" if value else "FAIL"


def format_report(result: ScanResult) -> str:
    lines = [
        f"WavPack block scan: {result.path}",
        (
            f"file_size={result.file_size} "
            f"declared_total_samples={result.declared_total_samples} "
            f"total_samples_consistent={str(result.total_samples_consistent).lower()}"
        ),
        "",
        (
            "idx  byte_off  id     flags       mode    sample_index  "
            "samples  crc         header_crc  block_csum  result"
        ),
    ]

    for index, record in enumerate(result.records):
        header = record.header
        status = "OK" if record.accepted else f"SKIP: {record.skip_reason}"
        lines.append(
            f"{index:<4} "
            f"0x{header.offset:08X}  "
            f"{header.block_id.decode('ascii'):<6} "
            f"0x{header.flags:08X}  "
            f"{header.mode:<7} "
            f"{header.block_index:<12} "
            f"{header.block_samples:<8} "
            f"0x{header.crc:08X}  "
            f"{_ok(record.header_crc_ok):<10} "
            f"{_ok(record.metadata.checksum_ok):<10} "
            f"{status}"
        )
        if not record.accepted:
            resume = (
                f"0x{record.resume_offset:X}"
                if record.resume_offset is not None else "none"
            )
            lines.append(
                f"     damaged block start=0x{header.offset:X} "
                f"resync={resume} skipped_bytes={record.skipped_bytes}"
            )

    lines.extend(
        [
            "",
            (
                f"summary: blocks={len(result.records)} "
                f"accepted={len(result.accepted_blocks)} "
                f"skipped={result.skipped_blocks} "
                f"checksum_failures={result.checksum_failures} "
                f"declared_total_samples={result.declared_total_samples} "
                f"total_samples_consistent={str(result.total_samples_consistent).lower()}"
            ),
            f"recovered_sample_indices={result.recovered_sample_indices}",
            f"recovered_sample_span={result.recovered_sample_span}",
        ]
    )
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Parse WavPack block headers and recover from damaged blocks. "
            "Raw PCM is optional and is used only to verify each header CRC."
        )
    )
    parser.add_argument("file", type=Path, help="WavPack (.wv) file")
    parser.add_argument(
        "--pcm",
        type=Path,
        help="raw decoded PCM oracle (signed little-endian, native WavPack width)",
    )
    parser.add_argument("--json", type=Path, help="write the complete scan as JSON")
    parser.add_argument(
        "--fail-on-error",
        action="store_true",
        help="return exit status 1 when any block was skipped or failed",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    oracle = args.pcm.read_bytes() if args.pcm else None
    result = scan_file(args.file, oracle)
    print(format_report(result), end="")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(result.to_dict(), indent=2) + "\n", encoding="utf-8"
        )

    if args.fail_on_error and result.skipped_blocks:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
