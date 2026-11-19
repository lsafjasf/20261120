"""Small ISO BMFF reader for the video samples used in this experiment."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import struct
from typing import Iterator


class MP4Error(ValueError):
    """Raised when an MP4 structure needed by the experiment is malformed."""


@dataclass(frozen=True)
class Box:
    type: str
    start: int
    header_size: int
    size: int

    @property
    def body_start(self) -> int:
        return self.start + self.header_size

    @property
    def end(self) -> int:
        return self.start + self.size


@dataclass(frozen=True)
class Sample:
    track_id: int
    index: int
    offset: int
    size: int
    dts: int
    duration: int

    @property
    def end(self) -> int:
        return self.offset + self.size

    @property
    def key(self) -> tuple[int, int, int, int]:
        return (self.track_id, self.offset, self.size, self.dts)


def _u32(data: bytes, offset: int) -> int:
    if offset + 4 > len(data):
        raise MP4Error(f"truncated uint32 at byte {offset}")
    return struct.unpack_from(">I", data, offset)[0]


def _i32(data: bytes, offset: int) -> int:
    if offset + 4 > len(data):
        raise MP4Error(f"truncated int32 at byte {offset}")
    return struct.unpack_from(">i", data, offset)[0]


def iter_boxes(data: bytes, start: int = 0, end: int | None = None) -> Iterator[Box]:
    """Yield sibling boxes without loading or copying their payloads."""
    limit = len(data) if end is None else end
    cursor = start
    while cursor < limit:
        if cursor + 8 > limit:
            raise MP4Error(f"truncated box header at byte {cursor}")
        size = _u32(data, cursor)
        box_type = data[cursor + 4 : cursor + 8].decode("latin-1")
        header_size = 8
        if size == 1:
            if cursor + 16 > limit:
                raise MP4Error(f"truncated large box header at byte {cursor}")
            size = struct.unpack_from(">Q", data, cursor + 8)[0]
            header_size = 16
        elif size == 0:
            size = limit - cursor
        if size < header_size or cursor + size > limit:
            raise MP4Error(
                f"invalid {box_type!r} box at byte {cursor}: size={size}, limit={limit}"
            )
        yield Box(box_type, cursor, header_size, size)
        cursor += size


def _children(data: bytes, parent: Box) -> dict[str, Box]:
    return {box.type: box for box in iter_boxes(data, parent.body_start, parent.end)}


def _parse_tfhd(
    data: bytes, box: Box, moof_start: int
) -> tuple[int, int, int, int, bool]:
    flags = _u32(data, box.body_start) & 0xFFFFFF
    cursor = box.body_start + 4
    track_id = _u32(data, cursor)
    cursor += 4

    base_offset = moof_start
    default_duration = 0
    default_size = 0
    default_base_is_moof = bool(flags & 0x020000)
    if flags & 0x000001:
        base_offset = struct.unpack_from(">Q", data, cursor)[0]
        cursor += 8
    if flags & 0x000002:
        cursor += 4
    if flags & 0x000008:
        default_duration = _u32(data, cursor)
        cursor += 4
    if flags & 0x000010:
        default_size = _u32(data, cursor)
        cursor += 4
    if default_base_is_moof:
        base_offset = moof_start
    return track_id, base_offset, default_duration, default_size, default_base_is_moof


def _parse_trun(
    data: bytes,
    box: Box,
    track_id: int,
    base_offset: int,
    default_duration: int,
    default_size: int,
    dts: int,
) -> tuple[list[Sample], int]:
    version = data[box.body_start]
    flags = _u32(data, box.body_start) & 0xFFFFFF
    cursor = box.body_start + 4
    sample_count = _u32(data, cursor)
    cursor += 4
    data_offset = 0
    if flags & 0x000001:
        data_offset = _i32(data, cursor)
        cursor += 4
    if flags & 0x000004:
        cursor += 4

    sample_offset = base_offset + data_offset
    samples: list[Sample] = []
    for index in range(sample_count):
        duration = default_duration
        size = default_size
        if flags & 0x000100:
            duration = _u32(data, cursor)
            cursor += 4
        if flags & 0x000200:
            size = _u32(data, cursor)
            cursor += 4
        if flags & 0x000400:
            cursor += 4
        if flags & 0x000800:
            cursor += 4
        if size <= 0:
            raise MP4Error(f"sample {index} in trun has non-positive size {size}")
        if sample_offset + size > len(data):
            raise MP4Error(
                f"sample {index} range [{sample_offset}, {sample_offset + size}) "
                f"exceeds file size {len(data)}"
            )
        samples.append(
            Sample(track_id, index, sample_offset, size, dts, duration)
        )
        sample_offset += size
        dts += duration
    return samples, dts


def parse_samples(data: bytes) -> list[Sample]:
    """Return fragmented MP4 samples in file/decode order."""
    samples: list[Sample] = []
    dts_by_track: dict[int, int] = {}
    for top in iter_boxes(data):
        if top.type != "moof":
            continue
        for traf in iter_boxes(data, top.body_start, top.end):
            if traf.type != "traf":
                continue
            children = _children(data, traf)
            if "tfhd" not in children or "trun" not in children:
                raise MP4Error("traf must contain both tfhd and trun")
            track_id, base, default_duration, default_size, _ = _parse_tfhd(
                data, children["tfhd"], top.start
            )
            dts = dts_by_track.get(track_id, 0)
            parsed, dts = _parse_trun(
                data,
                children["trun"],
                track_id,
                base,
                default_duration,
                default_size,
                dts,
            )
            samples.extend(parsed)
            dts_by_track[track_id] = dts
    if not samples:
        raise MP4Error("no fragmented media samples found")
    return sorted(samples, key=lambda sample: (sample.offset, sample.dts))


def sample_digest(data: bytes, sample: Sample) -> str:
    return hashlib.sha256(data[sample.offset : sample.end]).hexdigest()


def duplicate_keys(samples: list[Sample]) -> list[tuple[int, int, int, int]]:
    seen: set[tuple[int, int, int, int]] = set()
    duplicates: list[tuple[int, int, int, int]] = []
    for sample in samples:
        if sample.key in seen:
            duplicates.append(sample.key)
        seen.add(sample.key)
    return duplicates


def media_timescale(data: bytes) -> int:
    """Return the first media timescale declared by an ``mdhd`` box."""
    for top in iter_boxes(data):
        if top.type != "moov":
            continue
        for trak in iter_boxes(data, top.body_start, top.end):
            if trak.type != "trak":
                continue
            for mdia in iter_boxes(data, trak.body_start, trak.end):
                if mdia.type != "mdia":
                    continue
                for mdhd in iter_boxes(data, mdia.body_start, mdia.end):
                    if mdhd.type != "mdhd":
                        continue
                    version = data[mdhd.body_start]
                    field = mdhd.body_start + (20 if version == 1 else 12)
                    return _u32(data, field)
    raise MP4Error("no mdhd media timescale found")
