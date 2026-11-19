"""ISO-BMFF PSSH parsing and rewriting for ClearKey and PlayReady."""

from __future__ import annotations

import base64
import hashlib
import json
import struct
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Iterable

KID_SIZE = 16
SYSTEM_IDS = {
    "clearkey": uuid.UUID("1077efec-c0b2-4d02-ace3-3c1e52e2fb4b").bytes,
    "playready": uuid.UUID("9a04f079-9840-4286-ab92-e65be0885f95").bytes,
}
SYSTEM_NAMES = {value: name for name, value in SYSTEM_IDS.items()}
PLAYREADY_NS = "http://schemas.microsoft.com/DRM/2007/03/PlayReadyHeader"


class PSSHError(ValueError):
    """Raised when a PSSH box or its provider payload is malformed."""


def as_kid(value: str | bytes) -> bytes:
    if isinstance(value, bytes):
        if len(value) != KID_SIZE:
            raise PSSHError(f"KID must be {KID_SIZE} bytes, got {len(value)}")
        return value
    normalized = value.replace("-", "")
    if len(normalized) != KID_SIZE * 2:
        raise PSSHError(f"KID must be 32 hex characters, got {value!r}")
    try:
        return bytes.fromhex(normalized)
    except ValueError as exc:
        raise PSSHError(f"KID is not hexadecimal: {value!r}") from exc


def kid_hex(value: str | bytes) -> str:
    return as_kid(value).hex()


def kid_uuid(value: str | bytes) -> str:
    return str(uuid.UUID(bytes=as_kid(value)))


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding)
    except (ValueError, TypeError) as exc:
        raise PSSHError(f"invalid base64url KID: {value!r}") from exc


def clearkey_kid_b64(value: str | bytes) -> str:
    return _b64url_encode(as_kid(value))


@dataclass(frozen=True)
class PSSHBox:
    system_id: bytes
    version: int
    flags: int
    kids: tuple[bytes, ...]
    data: bytes

    @property
    def system_name(self) -> str:
        return SYSTEM_NAMES.get(self.system_id, "unknown")

    def serialize(self) -> bytes:
        if self.version not in (0, 1):
            raise PSSHError(f"unsupported PSSH version {self.version}")
        payload = bytes((self.version,)) + self.flags.to_bytes(3, "big")
        payload += self.system_id
        if self.version == 1:
            payload += struct.pack(">I", len(self.kids))
            payload += b"".join(as_kid(kid) for kid in self.kids)
        payload += struct.pack(">I", len(self.data)) + self.data
        size = 8 + len(payload)
        return struct.pack(">I4s", size, b"pssh") + payload

    @classmethod
    def parse(cls, raw: bytes, offset: int = 0) -> tuple["PSSHBox", int]:
        if offset < 0 or offset + 8 > len(raw):
            raise PSSHError("truncated PSSH box header")
        size = struct.unpack_from(">I", raw, offset)[0]
        box_type = raw[offset + 4 : offset + 8]
        header_size = 8
        if size == 1:
            if offset + 16 > len(raw):
                raise PSSHError("truncated extended PSSH box header")
            size = struct.unpack_from(">Q", raw, offset + 8)[0]
            header_size = 16
        elif size == 0:
            size = len(raw) - offset
        if size < header_size + 20 or offset + size > len(raw):
            raise PSSHError(f"invalid PSSH box size {size}")
        if box_type != b"pssh":
            raise PSSHError(f"expected pssh, got {box_type!r}")

        cursor = offset + header_size
        version = raw[cursor]
        flags = int.from_bytes(raw[cursor + 1 : cursor + 4], "big")
        cursor += 4
        if cursor + KID_SIZE + 4 > offset + size:
            raise PSSHError("truncated PSSH system id or data size")
        system_id = raw[cursor : cursor + KID_SIZE]
        cursor += KID_SIZE
        kids: list[bytes] = []
        if version > 0:
            if cursor + 4 > offset + size:
                raise PSSHError("truncated PSSH KID count")
            kid_count = struct.unpack_from(">I", raw, cursor)[0]
            cursor += 4
            if kid_count > 1024 or cursor + kid_count * KID_SIZE > offset + size:
                raise PSSHError(f"invalid PSSH KID count {kid_count}")
            kids = [
                raw[cursor + index * KID_SIZE : cursor + (index + 1) * KID_SIZE]
                for index in range(kid_count)
            ]
            cursor += kid_count * KID_SIZE
        if cursor + 4 > offset + size:
            raise PSSHError("truncated PSSH data size")
        data_size = struct.unpack_from(">I", raw, cursor)[0]
        cursor += 4
        if cursor + data_size > offset + size:
            raise PSSHError("PSSH data exceeds box boundary")
        data = raw[cursor : cursor + data_size]
        cursor += data_size
        if cursor != offset + size:
            raise PSSHError("PSSH box has trailing bytes outside data_size")
        return cls(system_id, version, flags, tuple(kids), data), size

    @classmethod
    def from_bytes(cls, raw: bytes) -> "PSSHBox":
        box, consumed = cls.parse(raw)
        if consumed != len(raw):
            raise PSSHError("trailing bytes after PSSH box")
        return box

    def provider_kids(self) -> tuple[bytes, ...]:
        if self.system_id == SYSTEM_IDS["clearkey"]:
            return clear_key_payload_kids(self.data)
        if self.system_id == SYSTEM_IDS["playready"]:
            return playready_payload_kids(self.data)
        return ()

    def all_kids(self) -> tuple[bytes, ...]:
        return tuple(dict.fromkeys(self.kids + self.provider_kids()))


def build_clearkey_data(kid: str | bytes) -> bytes:
    payload = {
        "kids": [clearkey_kid_b64(kid)],
        "type": "temporary",
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def clear_key_payload_kids(data: bytes) -> tuple[bytes, ...]:
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PSSHError("ClearKey PSSH data is not valid JSON") from exc
    raw_kids = payload.get("kids") if isinstance(payload, dict) else None
    if not isinstance(raw_kids, list) or not raw_kids:
        raise PSSHError("ClearKey PSSH data has no kids array")
    kids = tuple(_b64url_decode(value) for value in raw_kids)
    if any(len(kid) != KID_SIZE for kid in kids):
        raise PSSHError("ClearKey PSSH contains a KID with invalid length")
    return kids


def _playready_header(kid: str | bytes, la_url: str) -> bytes:
    kid_value = base64.b64encode(as_kid(kid)).decode("ascii")
    xml = (
        f'<WRMHEADER xmlns="{PLAYREADY_NS}" version="4.0.0.0"><DATA>'
        '<PROTECTINFO><KID ALGID="AESCTR" '
        f'VALUE="{kid_value}"/></PROTECTINFO>'
        f"<LA_URL>{la_url}</LA_URL>"
        "</DATA></WRMHEADER>"
    )
    return b"\xff\xfe" + xml.encode("utf-16-le")


def build_playready_data(
    kid: str | bytes, la_url: str = "https://license.example.test/playready"
) -> bytes:
    header = _playready_header(kid, la_url)
    record = struct.pack("<HH", 1, len(header)) + header
    return struct.pack("<IH", 6 + len(record), 1) + record


def parse_playready_object(data: bytes) -> list[tuple[int, bytes]]:
    if len(data) < 6:
        raise PSSHError("truncated PlayReady Object")
    declared, count = struct.unpack_from("<IH", data)
    if declared != len(data):
        raise PSSHError(
            f"PlayReady Object length {declared} does not match data {len(data)}"
        )
    if count > 256:
        raise PSSHError(f"invalid PlayReady Object record count {count}")
    cursor = 6
    records: list[tuple[int, bytes]] = []
    for _ in range(count):
        if cursor + 4 > len(data):
            raise PSSHError("truncated PlayReady Object record header")
        record_type, record_length = struct.unpack_from("<HH", data, cursor)
        cursor += 4
        if cursor + record_length > len(data):
            raise PSSHError("PlayReady Object record exceeds object boundary")
        records.append((record_type, data[cursor : cursor + record_length]))
        cursor += record_length
    if cursor != len(data):
        raise PSSHError("trailing bytes in PlayReady Object")
    return records


def playready_header_xml(data: bytes) -> str:
    for record_type, record in parse_playready_object(data):
        if record_type == 1:
            if record.startswith(b"\xff\xfe"):
                return record.decode("utf-16")
            return record.decode("utf-16-le")
    raise PSSHError("PlayReady Object has no record type 1 (header)")


def playready_payload_kids(data: bytes) -> tuple[bytes, ...]:
    text = playready_header_xml(data)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise PSSHError("PlayReady header XML is malformed") from exc
    kids: list[bytes] = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "KID":
            continue
        value = element.attrib.get("VALUE")
        if not value:
            raise PSSHError("PlayReady KID element has no VALUE")
        try:
            kid = base64.b64decode(value, validate=True)
        except (ValueError, TypeError) as exc:
            raise PSSHError("PlayReady KID is not valid base64") from exc
        if len(kid) != KID_SIZE:
            raise PSSHError("PlayReady KID has invalid length")
        kids.append(kid)
    if not kids:
        raise PSSHError("PlayReady header XML has no KID")
    return tuple(kids)


def build_pssh(
    provider: str,
    kid: str | bytes,
    *,
    version: int = 1,
    flags: int = 0,
    la_url: str = "https://license.example.test/playready",
) -> PSSHBox:
    provider = provider.lower()
    if provider == "clearkey":
        system_id = SYSTEM_IDS["clearkey"]
        data = build_clearkey_data(kid)
    elif provider == "playready":
        system_id = SYSTEM_IDS["playready"]
        data = build_playready_data(kid, la_url)
    else:
        raise PSSHError(f"unsupported PSSH provider {provider!r}")
    kids = (as_kid(kid),) if version == 1 else ()
    return PSSHBox(system_id, version, flags, kids, data)


def rewrite_pssh(box: PSSHBox, kid: str | bytes) -> PSSHBox:
    provider = box.system_name
    if provider not in SYSTEM_IDS:
        raise PSSHError(f"cannot rewrite unknown PSSH system {kid_uuid(box.system_id)}")
    la_url = "https://license.example.test/playready"
    if provider == "playready":
        text = playready_header_xml(box.data)
        marker_start = text.find("<LA_URL>")
        marker_end = text.find("</LA_URL>")
        if marker_start >= 0 and marker_end > marker_start:
            la_url = text[marker_start + len("<LA_URL>") : marker_end]
    return build_pssh(
        provider,
        kid,
        version=box.version,
        flags=box.flags,
        la_url=la_url,
    )


def pssh_summary(box: PSSHBox) -> dict[str, object]:
    return {
        "provider": box.system_name,
        "system_id": kid_uuid(box.system_id),
        "version": box.version,
        "flags": box.flags,
        "header_kids": [kid_hex(kid) for kid in box.kids],
        "payload_kids": [kid_hex(kid) for kid in box.provider_kids()],
        "box_size": len(box.serialize()),
        "sha256": hashlib.sha256(box.serialize()).hexdigest(),
    }


def unique_kids(boxes: Iterable[PSSHBox]) -> tuple[bytes, ...]:
    values: list[bytes] = []
    for box in boxes:
        for kid in box.all_kids():
            if kid not in values:
                values.append(kid)
    return tuple(values)
