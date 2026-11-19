"""ISO-BMFF ``pssh`` box parsing and KID rewriting.

Two flavours are supported:

* version-1 Common System / ClearKey boxes whose KIDs live in the box payload;
* Widevine boxes whose KIDs appear both in the v1 box payload and in the
  inner Widevine Protobuf (``license``/``key_id`` fields).

The rewrite replaces every KID with the project default KID while keeping the
  system id, version, flags, box size and every non-KID payload byte intact.
"""

from __future__ import annotations

from dataclasses import dataclass, field

WIDEVINE_SYSTEM_ID = bytes.fromhex("edef8ba979d64acea3c827dcd51d21ed")
CLEARKEY_SYSTEM_ID = bytes.fromhex("1077efecc0b24d02ace33c1e52e2fb4b")
DEFAULT_KID = "01234567-89ab-cdef-0123-456789abcdef"


def kid_to_bytes(kid: str) -> bytes:
    return bytes.fromhex(kid.replace("-", ""))


def bytes_to_kid(raw: bytes) -> str:
    if len(raw) != 16:
        raise ValueError(f"KID must be 16 bytes, got {len(raw)}")
    hexed = raw.hex()
    return "-".join(
        (hexed[0:8], hexed[8:12], hexed[12:16], hexed[16:20], hexed[20:32])
    )


@dataclass
class PsshBox:
    size: int
    version: int
    flags: int
    system_id: bytes
    box_kids: list[bytes] = field(default_factory=list)
    data: bytes = b""

    @property
    def system_name(self) -> str:
        if self.system_id == WIDEVINE_SYSTEM_ID:
            return "widevine"
        if self.system_id == CLEARKEY_SYSTEM_ID:
            return "clearkey"
        return "unknown"


def parse_pssh(blob: bytes) -> PsshBox:
    """Parse a single ``pssh`` box (compact 32-bit size only)."""
    if len(blob) < 32 or blob[4:8] != b"pssh":
        raise ValueError("not a pssh box")
    size = int.from_bytes(blob[0:4], "big")
    if size != len(blob):
        raise ValueError(f"size field {size} != blob length {len(blob)}")
    version = blob[8]
    flags = int.from_bytes(blob[9:12], "big")
    system_id = blob[12:28]
    offset = 28
    box_kids: list[bytes] = []
    if version == 1:
        count = int.from_bytes(blob[offset:offset + 4], "big")
        offset += 4
        for _ in range(count):
            box_kids.append(blob[offset:offset + 16])
            offset += 16
    data_size = int.from_bytes(blob[offset:offset + 4], "big")
    offset += 4
    data = blob[offset:offset + data_size]
    if offset + data_size != size:
        raise ValueError("trailing bytes after pssh payload")
    return PsshBox(size, version, flags, system_id, box_kids, data)


def build_pssh(box: PsshBox) -> bytes:
    payload = bytearray()
    payload.append(box.version)
    payload.extend(box.flags.to_bytes(3, "big"))
    payload.extend(box.system_id)
    if box.version == 1:
        payload.extend(len(box.box_kids).to_bytes(4, "big"))
        for kid in box.box_kids:
            payload.extend(kid)
    payload.extend(len(box.data).to_bytes(4, "big"))
    payload.extend(box.data)
    total = 8 + len(payload)
    return total.to_bytes(4, "big") + b"pssh" + bytes(payload)


def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while True:
        byte = buf[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
        shift += 7


def _encode_varint(value: int) -> bytes:
    out = bytearray()
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def iter_proto_fields(buf: bytes):
    """Yield ``(field_number, wire_type, value, raw_bytes)`` tuples."""
    pos = 0
    while pos < len(buf):
        tag, pos = _read_varint(buf, pos)
        field_number = tag >> 3
        wire_type = tag & 0x07
        start = pos
        if wire_type == 0:
            value, pos = _read_varint(buf, pos)
            raw = buf[start:pos]
        elif wire_type == 2:
            length, pos = _read_varint(buf, pos)
            value = buf[pos:pos + length]
            pos += length
            raw = buf[start:pos]
        elif wire_type == 1:
            value = buf[pos:pos + 8]
            pos += 8
            raw = buf[start:pos]
        elif wire_type == 5:
            value = buf[pos:pos + 4]
            pos += 4
            raw = buf[start:pos]
        else:
            raise ValueError(f"unsupported protobuf wire type {wire_type}")
        yield field_number, wire_type, value, raw


def widevine_proto_kids(data: bytes) -> list[bytes]:
    """Collect Widevine PSSH ``key_id`` values (field 2, length-delimited)."""
    kids: list[bytes] = []
    for field_number, wire_type, value, _ in iter_proto_fields(data):
        if field_number == 2 and wire_type == 2 and len(value) == 16:
            kids.append(value)
    return kids


def rewrite_widevine_proto(data: bytes, new_kid: bytes) -> tuple[bytes, list[int]]:
    """Replace every field-2 16-byte key id; preserve all other fields.

    Returns the rewritten bytes and the sorted list of preserved field numbers.
    """
    out = bytearray()
    preserved: set[int] = set()
    for field_number, wire_type, value, _ in iter_proto_fields(data):
        tag = _encode_varint((field_number << 3) | wire_type)
        if field_number == 2 and wire_type == 2 and len(value) == 16:
            out += tag
            out += _encode_varint(16)
            out += new_kid
        else:
            preserved.add(field_number)
            out += tag
            if wire_type == 2:
                out += _encode_varint(len(value))
                out += value
            else:
                if wire_type == 0:
                    out += _encode_varint(value)
                else:
                    out += value
    return bytes(out), sorted(preserved)


def rewrite_pssh(blob: bytes, target_kid: str = DEFAULT_KID) -> tuple[
    bytes, dict
]:
    """Rewrite all KIDs (box-level and Widevine protobuf) in one PSSH box."""
    box = parse_pssh(blob)
    new_kid = kid_to_bytes(target_kid)
    report = {
        "system": box.system_name,
        "original_box_kids": [bytes_to_kid(k) for k in box.box_kids],
        "original_proto_kids": [],
        "rewritten_box_kids": [],
        "rewritten_proto_kids": [],
        "preserved_fields": [],
    }

    if box.system_name == "widevine":
        report["original_proto_kids"] = [
            bytes_to_kid(k) for k in widevine_proto_kids(box.data)
        ]
        box.data, report["preserved_fields"] = rewrite_widevine_proto(
            box.data, new_kid
        )
        report["rewritten_proto_kids"] = [
            bytes_to_kid(k) for k in widevine_proto_kids(box.data)
        ]

    box.box_kids = [new_kid for _ in box.box_kids]
    report["rewritten_box_kids"] = [bytes_to_kid(k) for k in box.box_kids]
    rewritten = build_pssh(box)
    if len(rewritten) != len(blob):
        raise AssertionError("rewrite changed the box length")
    return rewritten, report


def parse_concatenated_pssh(blob: bytes) -> list[PsshBox]:
    """Parse a stream of back-to-back PSSH boxes (e.g. cenc initData)."""
    boxes: list[PsshBox] = []
    pos = 0
    while pos < len(blob):
        size = int.from_bytes(blob[pos:pos + 4], "big")
        boxes.append(parse_pssh(blob[pos:pos + size]))
        pos += size
    return boxes
