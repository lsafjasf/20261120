"""Minimal ISO-BMFF inspection, rewriting, and byte-level auditing."""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass

from .pssh import KID_SIZE, PSSHBox, as_kid, kid_hex

CONTAINER_TYPES = {
    b"moov",
    b"trak",
    b"mdia",
    b"minf",
    b"stbl",
    b"edts",
    b"dinf",
    b"mvex",
    b"moof",
    b"traf",
    b"mfra",
    b"sinf",
    b"schi",
}
VISUAL_SAMPLE_ENTRIES = {
    b"avc1",
    b"avc3",
    b"hvc1",
    b"hev1",
    b"encv",
    b"vp08",
    b"vp09",
    b"av01",
}


class MediaError(ValueError):
    """Raised when media boxes cannot be parsed or rewritten safely."""


@dataclass(frozen=True)
class Box:
    type: bytes
    start: int
    size: int
    header_size: int = 8

    @property
    def end(self) -> int:
        return self.start + self.size

    @property
    def payload_start(self) -> int:
        return self.start + self.header_size

    @property
    def payload(self) -> slice:
        return slice(self.payload_start, self.end)


def _read_box(data: bytes, offset: int, limit: int) -> Box:
    if offset + 8 > limit:
        raise MediaError(f"truncated box header at offset {offset}")
    size, box_type = struct.unpack_from(">I4s", data, offset)
    header_size = 8
    if size == 1:
        if offset + 16 > limit:
            raise MediaError(f"truncated extended box header at offset {offset}")
        size = struct.unpack_from(">Q", data, offset + 8)[0]
        header_size = 16
    elif size == 0:
        size = limit - offset
    if size < header_size or offset + size > limit:
        raise MediaError(f"invalid box {box_type!r} size {size} at offset {offset}")
    return Box(box_type, offset, size, header_size)


def iter_boxes(data: bytes, start: int = 0, end: int | None = None):
    limit = len(data) if end is None else end
    cursor = start
    while cursor < limit:
        box = _read_box(data, cursor, limit)
        yield box
        cursor = box.end
    if cursor != limit:
        raise MediaError("box range does not end on a box boundary")


def top_level_boxes(data: bytes) -> tuple[Box, ...]:
    return tuple(iter_boxes(data))


def _walk_boxes(
    data: bytes, start: int, end: int, path: str = ""
) -> list[tuple[str, Box]]:
    found: list[tuple[str, Box]] = []
    for box in iter_boxes(data, start, end):
        box_path = f"{path}/{box.type.decode('latin-1')}"
        found.append((box_path, box))
        if box.type in CONTAINER_TYPES:
            found.extend(_walk_boxes(data, box.payload_start, box.end, box_path))
        elif box.type == b"stsd":
            found.extend(_walk_stsd(data, box, box_path))
    return found


def _walk_stsd(data: bytes, box: Box, path: str) -> list[tuple[str, Box]]:
    if box.payload_start + 8 > box.end:
        raise MediaError("truncated stsd box")
    entry_count = struct.unpack_from(">I", data, box.payload_start + 4)[0]
    cursor = box.payload_start + 8
    found: list[tuple[str, Box]] = []
    for _ in range(entry_count):
        entry = _read_box(data, cursor, box.end)
        entry_path = f"{path}/{entry.type.decode('latin-1')}"
        found.append((entry_path, entry))
        if entry.type in VISUAL_SAMPLE_ENTRIES:
            child_start = entry.payload_start + 78
            if child_start > entry.end:
                raise MediaError("truncated visual sample entry")
            found.extend(_walk_boxes(data, child_start, entry.end, entry_path))
        cursor = entry.end
    if cursor != box.end:
        raise MediaError("stsd entries do not fill the box")
    return found


def find_boxes(data: bytes, box_type: bytes) -> tuple[Box, ...]:
    return tuple(box for _, box in _walk_boxes(data, 0, len(data)) if box.type == box_type)


def find_top_level_boxes(data: bytes, box_type: bytes) -> tuple[Box, ...]:
    return tuple(box for box in top_level_boxes(data) if box.type == box_type)


def default_kid(data: bytes) -> bytes:
    boxes = find_boxes(data, b"tenc")
    if len(boxes) != 1:
        raise MediaError(f"expected one tenc box, found {len(boxes)}")
    box = boxes[0]
    if box.size < box.header_size + 4 + KID_SIZE:
        raise MediaError("tenc box is too short to contain default_KID")
    kid = data[box.end - KID_SIZE : box.end]
    if len(kid) != KID_SIZE:
        raise MediaError("invalid tenc default_KID")
    return kid


def parse_pssh_boxes(data: bytes) -> tuple[PSSHBox, ...]:
    boxes = find_top_level_boxes(data, b"pssh")
    return tuple(PSSHBox.from_bytes(data[box.start : box.end]) for box in boxes)


def insert_top_level_after(data: bytes, anchor: bytes, new_box: bytes) -> bytes:
    boxes = top_level_boxes(data)
    for index, box in enumerate(boxes):
        if box.type == anchor:
            return data[: box.end] + new_box + data[box.end :]
    raise MediaError(f"top-level anchor {anchor!r} not found")


def replace_top_level_box(data: bytes, index: int, replacement: bytes) -> bytes:
    pssh_boxes = find_top_level_boxes(data, b"pssh")
    if index < 0 or index >= len(pssh_boxes):
        raise MediaError(f"PSSH index {index} is out of range")
    box = pssh_boxes[index]
    if len(replacement) != box.size:
        raise MediaError(
            "byte-audited rewrite requires an equal-size PSSH replacement "
            f"({len(replacement)} != {box.size})"
        )
    return data[: box.start] + replacement + data[box.end :]


def patch_default_kid(data: bytes, kid: str | bytes) -> bytes:
    boxes = find_boxes(data, b"tenc")
    if len(boxes) != 1:
        raise MediaError(f"expected one tenc box, found {len(boxes)}")
    box = boxes[0]
    value = as_kid(kid)
    return data[: box.end - KID_SIZE] + value + data[box.end :]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def mdat_digest(data: bytes) -> dict[str, object]:
    boxes = find_top_level_boxes(data, b"mdat")
    digest = hashlib.sha256()
    size = 0
    for box in boxes:
        payload = data[box.payload_start : box.end]
        digest.update(payload)
        size += len(payload)
    return {"payload_size": size, "sha256": digest.hexdigest(), "box_count": len(boxes)}


def changed_ranges(before: bytes, after: bytes) -> list[dict[str, object]]:
    if len(before) != len(after):
        raise MediaError("changed_ranges requires equal-size byte strings")
    ranges: list[dict[str, object]] = []
    cursor = 0
    while cursor < len(before):
        if before[cursor] == after[cursor]:
            cursor += 1
            continue
        start = cursor
        while cursor < len(before) and before[cursor] != after[cursor]:
            cursor += 1
        ranges.append(
            {
                "offset": start,
                "length": cursor - start,
                "before_hex": before[start:cursor].hex(),
                "after_hex": after[start:cursor].hex(),
            }
        )
    return ranges


def license_field_ranges(data: bytes) -> list[dict[str, object]]:
    ranges: list[dict[str, object]] = []
    for index, box in enumerate(find_top_level_boxes(data, b"pssh")):
        ranges.append(
            {
                "start": box.start,
                "end": box.end,
                "field": f"pssh[{index}]",
            }
        )
    tenc_boxes = find_boxes(data, b"tenc")
    if len(tenc_boxes) == 1:
        box = tenc_boxes[0]
        ranges.append(
            {
                "start": box.end - KID_SIZE,
                "end": box.end,
                "field": "tenc.default_KID",
            }
        )
    return ranges


def _describe_offset(boxes: tuple[Box, ...], offset: int) -> str:
    for box in boxes:
        if box.start <= offset < box.end:
            return box.type.decode("latin-1")
    return "unknown"


def audit_rewrite(before: bytes, after: bytes, provider: str) -> dict[str, object]:
    if len(before) != len(after):
        raise MediaError("audit_rewrite requires equal-size media")
    before_top = top_level_boxes(before)
    after_top = top_level_boxes(after)
    layout_identical = [
        (box.type, box.start, box.size) for box in before_top
    ] == [(box.type, box.start, box.size) for box in after_top]
    ranges = changed_ranges(before, after)
    allowed = license_field_ranges(before)
    allowed_spans = [(item["start"], item["end"], item["field"]) for item in allowed]
    outside: list[dict[str, object]] = []
    for change in ranges:
        start = int(change["offset"])
        end = start + int(change["length"])
        fully_allowed = any(start >= low and end <= high for low, high, _ in allowed_spans)
        labels = [
            field for low, high, field in allowed_spans if start < high and end > low
        ]
        change["license_fields"] = labels
        change["top_level_box"] = _describe_offset(before_top, start)
        if not fully_allowed:
            outside.append(change)
    before_mdat = mdat_digest(before)
    after_mdat = mdat_digest(after)
    before_pssh = parse_pssh_boxes(before)
    after_pssh = parse_pssh_boxes(after)
    return {
        "provider": provider,
        "before_size": len(before),
        "after_size": len(after),
        "same_length": len(before) == len(after),
        "layout_identical": layout_identical,
        "changed_byte_count": sum(int(item["length"]) for item in ranges),
        "changed_ranges": ranges,
        "license_field_ranges": allowed,
        "changed_outside_license_fields": outside,
        "only_license_fields_changed": not outside,
        "default_kid_before": kid_hex(default_kid(before)),
        "default_kid_after": kid_hex(default_kid(after)),
        "pssh_before_sha256": sha256(before_pssh[0].serialize()),
        "pssh_after_sha256": sha256(after_pssh[0].serialize()),
        "media_payload": {
            "before": before_mdat,
            "after": after_mdat,
            "unchanged": before_mdat == after_mdat,
        },
    }
